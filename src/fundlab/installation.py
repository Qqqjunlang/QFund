"""安装/卸载的互斥与任务保留；不迁移或删除用户数据。"""
from contextlib import ExitStack, nullcontext
import json
import os
from pathlib import Path, PurePosixPath
import sys
import time
from uuid import uuid4

from fundlab.collectors.session import ProfileLock
from fundlab.errors import FundlabError


class InstallationGate:
    """运行实例共享，安装维护独占；跨进程持有至整个操作结束。"""
    def __init__(self, config, *, exclusive=False):
        self.path = config.local_dir / 'installation.lock'
        self.exclusive = exclusive
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open('a+b')
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() == 0:
            self.handle.write(b'0'); self.handle.flush()
        try:
            if os.name == 'nt':
                import ctypes
                from ctypes import wintypes
                import msvcrt
                class Overlapped(ctypes.Structure):
                    _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t),
                                ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD), ('hEvent', wintypes.HANDLE)]
                self.overlap = Overlapped()
                self.native = wintypes.HANDLE(msvcrt.get_osfhandle(self.handle.fileno()))
                kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                self.kernel = kernel
                if not kernel.LockFileEx(self.native, 1 | (2 if self.exclusive else 0), 0, 1, 0, ctypes.byref(self.overlap)):
                    raise OSError(ctypes.get_last_error(), 'installation gate busy')
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_NB | (fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH))
        except OSError:
            self.handle.close(); self.handle = None
            raise FundlabError('INSTALLATION_BUSY', 'QFund仍在运行或已有安装维护操作，请等待任务完成并正常退出后重试。') from None
        return self

    def __exit__(self, *args):
        if self.handle is not None:
            if os.name == 'nt':
                import ctypes
                self.kernel.UnlockFileEx(self.native, 0, 1, 0, ctypes.byref(self.overlap))
            self.handle.close(); self.handle = None


def runtime_guard(config):
    return InstallationGate(config) if getattr(sys, 'frozen', False) else nullcontext()


def _write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def _bundle_files(app):
    try:
        data = json.loads((app / 'program-manifest.json').read_text(encoding='utf-8-sig'))
        if data['product'] != 'QFund' or data['schema'] != 1 or not data['files']:
            raise ValueError()
        result = {}
        for item in data['files']:
            relative = PurePosixPath(item['path'])
            if relative.is_absolute() or '..' in relative.parts or '\\' in item['path'] or ':' in item['path']:
                raise ValueError()
            # 清单只含程序包，不允许把数据/profile目录作为可管理文件。
            if relative.parts[0] not in {'QFund.exe', 'QFundWorker.exe', '_internal'}:
                raise ValueError()
            path = app.joinpath(*relative.parts)
            if not path.resolve().is_relative_to(app.resolve()):
                raise ValueError()
            result[path] = item['sha256']
        return result
    except (OSError, ValueError, TypeError, KeyError):
        raise FundlabError('INSTALL_MANIFEST_INVALID', '程序清单缺失或无法确认，请恢复完整安装包；未修改任务。') from None


def _hash(path):
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MaintenanceSession:
    def __init__(self, config, app, install_id, mode, *, task_factory=None):
        self.config, self.app = config, Path(app).resolve()
        self.install_id, self.mode = install_id, mode
        self.stack = ExitStack()
        self.before = None
        self.removed = False
        self.snapshot = {}
        self.snapshot_complete = True
        if task_factory is None:
            from fundlab.scheduling.windows_task import WindowsTask
            task_factory = WindowsTask
        self.task = task_factory(config, interpreter=self.app / 'QFundWorker.exe')

    def __enter__(self):
        if self.mode not in {'install', 'uninstall'}:
            raise FundlabError('INSTALL_MODE_INVALID', '安装维护模式不正确。')
        root = self.config.root.resolve()
        if self.app.is_relative_to(root) or root.is_relative_to(self.app):
            raise FundlabError('INSTALL_PATH_INVALID', '程序目录与数据目录必须分开，请选择其他安装位置。')
        marker_path = self.app / 'qfund-install.json'
        try:
            if marker_path.exists():
                marker = json.loads(marker_path.read_text(encoding='utf-8'))
                if (marker['product'] != 'QFund' or marker['install_id'] != self.install_id or
                    Path(marker['app_dir']).resolve() != self.app or Path(marker['data_root']).resolve() != root):
                    raise ValueError()
                try:
                    managed = _bundle_files(self.app)
                except FundlabError:
                    if self.mode != 'install':
                        raise
                    managed = {}; self.snapshot_complete = False
                self.snapshot = {path: _hash(path) for path in managed if path.is_file()}
                self.snapshot_complete = self.snapshot_complete and len(self.snapshot) == len(managed)
                self.snapshot[marker_path] = _hash(marker_path)
                if (self.app / 'program-manifest.json').is_file():
                    self.snapshot[self.app / 'program-manifest.json'] = _hash(self.app / 'program-manifest.json')
            elif self.mode == 'uninstall' or (self.app.exists() and any(self.app.iterdir())):
                raise ValueError()
        except (OSError, ValueError, TypeError, KeyError):
            raise FundlabError('INSTALL_TARGET_UNCONFIRMED', '所选目录不是可确认的QFund安装目录，请选择空目录或原安装位置；不会覆盖其他资料。') from None
        try:
            self.stack.enter_context(InstallationGate(self.config, exclusive=True))
            for name in ('web-server.lock', 'scheduler.lock', 'update.lock', 'edge-profile.lock'):
                self.stack.enter_context(ProfileLock(self.config.local_dir / name,
                    FundlabError('INSTALLATION_BUSY', '工作台、登录或数据任务仍在运行，请完成后正常退出再继续安装或卸载。')))
            self.before = self.task.query()
            if self.before['installed']:
                from fundlab.scheduling.settings import ScheduleSettings
                if self.before['state'] == 4 or not self.task.state(ScheduleSettings(self.config).read()['update_time'])['healthy']:
                    raise FundlabError('INSTALL_TASK_UNCONFIRMED', '自动任务正在运行或配置需核对，未继续安装或卸载。')
            journals = self.config.local_dir / 'installer-journal'
            journals.mkdir(exist_ok=True)
            self.journal = journals / (uuid4().hex + '.json')
            self.record = dict(schema=1, action=self.mode, app_dir=str(self.app), data_root=str(root),
                               task_before=self.before, status='preparing')
            _write_json(self.journal, self.record)
            if self.before['installed'] and self.before['enabled']:
                self.task.call('disable')
                if self.task.query()['enabled']:
                    raise FundlabError('INSTALL_TASK_UNCONFIRMED', '自动任务未能暂停，未继续安装。')
            self.record['status'] = 'ready'; _write_json(self.journal, self.record)
            return self
        except Exception:
            try:
                if hasattr(self, 'record'):
                    self.rollback()
            finally:
                self.stack.close()
            raise

    def remove_task(self):
        if self.mode != 'uninstall':
            raise FundlabError('INSTALL_MODE_INVALID', '此操作只能用于卸载。')
        self.task.call('remove')
        if self.task.query()['installed']:
            raise FundlabError('INSTALL_TASK_UNCONFIRMED', '自动任务尚未移除，已停止卸载。')
        self.removed = True
        self.record['status'] = 'task_removed'; _write_json(self.journal, self.record)

    def _restore_task(self):
        if self.before['installed']:
            self.task.call('register', xml=self.before['xml'])
            restored = self.task.query()
            if not restored['installed'] or restored['enabled'] != self.before['enabled']:
                raise FundlabError('INSTALL_TASK_UNCONFIRMED', '任务原状态恢复结果需核对，请按安装维护记录处理。')

    def commit(self):
        if self.mode == 'install':
            if any(not path.is_file() or _hash(path) != digest for path, digest in _bundle_files(self.app).items()):
                raise FundlabError('INSTALL_PROGRAM_INCOMPLETE', '程序文件未完整写入，未恢复自动任务；请保留安装记录。')
            _write_json(self.app / 'qfund-install.json', dict(product='QFund', schema=1, install_id=self.install_id,
                        app_dir=str(self.app), data_root=str(self.config.root.resolve())))
            self._restore_task()
        elif not self.removed:
            raise FundlabError('INSTALL_TASK_UNCONFIRMED', '卸载尚未完成任务移除检查。')
        self.record['status'] = 'committed'; _write_json(self.journal, self.record)

    def rollback(self):
        unchanged = self.snapshot_complete and all(path.is_file() and _hash(path) == value for path, value in self.snapshot.items())
        if self.before and self.before['installed']:
            if unchanged:
                self._restore_task()
            else:
                raw = self.task.query()
                if raw['installed']:
                    self.task.call('disable')
                self.record['status'] = 'needs_attention'
                _write_json(self.journal, self.record)
                raise FundlabError('INSTALL_ROLLBACK_UNCONFIRMED', '安装未完成且程序已变化，自动任务保持停用或已移除；请恢复完整程序后再启用。')
        self.record['status'] = 'rolled_back'; _write_json(self.journal, self.record)

    def __exit__(self, *args):
        self.stack.close()


def maintenance_main(config, app, install_id, mode, channel, *, timeout=900):
    """由安装器启动并持锁，使用其私有临时目录握手；不开放HTTP维护接口。"""
    channel = Path(channel)
    command = channel.with_suffix('.command')
    def status(state, message=''):
        temporary = channel.with_suffix('.tmp')
        temporary.write_text('[status]\nstate=' + state + '\nmessage=' + message.replace('\n', ' ') + '\n', encoding='utf-16')
        os.replace(temporary, channel)
    try:
        with MaintenanceSession(config, app, install_id, mode) as session:
            status('ready')
            end = time.monotonic() + timeout
            try:
                while time.monotonic() < end:
                    if not command.exists():
                        time.sleep(.05); continue
                    action = command.read_text(encoding='utf-8-sig').strip()
                    command.unlink()
                    if action == 'remove':
                        session.remove_task(); status('removed')
                    elif action == 'commit':
                        session.commit(); status('done'); return 0
                    elif action == 'rollback':
                        session.rollback(); status('done'); return 0
                    else:
                        raise FundlabError('INSTALL_COMMAND_INVALID', '安装维护指令无法确认。')
                raise FundlabError('INSTALL_TIMEOUT', '安装维护等待超时，请核对安装记录和自动任务状态。')
            except Exception:
                session.rollback()
                raise
    except (FundlabError, OSError, ValueError) as error:
        status('failed', str(error))
        return 1
