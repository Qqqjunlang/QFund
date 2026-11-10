"""仅管理由本项目路径命名、来源和启动动作共同识别的Windows任务。"""
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from fundlab.collectors.session import ProfileLock
from fundlab.errors import FundlabError

NS = 'http://schemas.microsoft.com/windows/2004/02/mit/task'
ET.register_namespace('', NS)

TASK_PERMISSION_CODES = {0x80070005, 0x80070522}
TASK_SERVICE_CODES = {0x80041315, 0x80041322, 0x80041323, 0x80070422, 0x800706BA}


def _task_failure(data):
    raw_code = data.get('hresult')
    hresult = raw_code & 0xFFFFFFFF if type(raw_code) is int and -(2**31) <= raw_code <= 0xFFFFFFFF else None
    known = {
        'TASK_OWNERSHIP_CONFLICT': ('WINDOWS_TASK_CONFLICT', '同名任务不属于本项目，已保留且未修改。'),
        'TASK_RUNNING': ('SCHEDULE_BUSY', '本项目的自动任务正在运行，请等待结束后重试。'),
        'TASK_NOT_ENABLED': ('WINDOWS_TASK_NOT_ENABLED', '本项目的自动任务尚未启用。'),
    }
    category = data.get('error')
    if isinstance(category, str) and category in known:
        code, message = known[category]
    elif hresult in TASK_PERMISSION_CODES:
        code, message = 'WINDOWS_TASK_PERMISSION', '当前用户没有所需的 Windows 任务权限，请使用原安装账户或联系管理员。'
    elif hresult in TASK_SERVICE_CODES or data.get('phase') == 'service':
        code, message = 'WINDOWS_TASK_SERVICE', '无法连接 Windows 任务计划服务，请检查服务状态后重试。'
    else:
        code, message = 'WINDOWS_TASK_FAILED', 'Windows 任务操作失败，请核对任务状态后重试。'
    if hresult is not None:
        message += f'（系统代码 0x{hresult:08X}）'
    return FundlabError(code, message)


def _protocol_failure():
    return FundlabError('WINDOWS_TASK_PROTOCOL', 'Windows 任务助手未返回有效结果，任务状态未确认。请先核对再重试。')


def transport(request):
    if sys.platform != 'win32':
        raise FundlabError('WINDOWS_TASK_UNAVAILABLE', '系统任务仅支持Windows，未修改任何任务。')
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    try:
        result = subprocess.run([str(powershell), '-NoLogo', '-NoProfile', '-NonInteractive',
                                 '-ExecutionPolicy', 'RemoteSigned', '-File',
                                 str(Path(__file__).with_suffix('.ps1'))],
                                input=json.dumps(request, ensure_ascii=False).encode('utf-8'),
                                capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise FundlabError('WINDOWS_TASK_TIMEOUT', 'Windows 任务检查或操作超时，结果未确认。请先核对任务状态再重试。') from None
    except PermissionError:
        raise FundlabError('WINDOWS_TASK_PERMISSION', '当前用户没有权限启动 Windows PowerShell，请联系管理员。') from None
    except OSError:
        raise FundlabError('WINDOWS_TASK_START_FAILED', '无法启动 Windows PowerShell，请检查系统组件后重试。') from None
    try:
        data = json.loads(result.stdout.decode('utf-8-sig'))
    except (UnicodeError, ValueError):
        # Windows PowerShell 有时以包装异常报告策略错误，保留帮助链接和错误 ID。
        diagnostics = result.stderr.lower()
        blocked = (b'pssecurityexception' in diagnostics
                   or (b'linkid=135170' in diagnostics and b'unauthorizedaccess' in diagnostics))
        if result.returncode and blocked:
            raise FundlabError('WINDOWS_TASK_POLICY', 'PowerShell 阻止了任务助手脚本。请检查组织执行策略或脚本来源，并联系管理员核对。') from None
        raise _protocol_failure() from None
    if not isinstance(data, dict):
        raise _protocol_failure()
    if data.get('ok') is False and result.returncode:
        raise _task_failure(data)
    if (result.returncode or data.get('ok') is not True or type(data.get('installed')) is not bool
            or not isinstance(data.get('sid'), str) or not data['sid']):
        raise _protocol_failure()
    if data['installed'] and (not isinstance(data.get('xml'), str) or not data['xml']
                              or type(data.get('enabled')) is not bool or type(data.get('state')) is not int):
        raise _protocol_failure()
    return data


class WindowsTask:
    def __init__(self, config, *, interpreter=None):
        self.config = config
        self.root = config.root.resolve()
        identity = hashlib.sha256(str(self.root).casefold().encode('utf-8')).hexdigest()
        self.name = 'Fundlab-Getdata-' + identity[:12]
        self.source = 'fundlab-schedule-v1:' + identity
        self.frozen = bool(getattr(sys, 'frozen', False))
        executable = Path(sys.executable).with_name('QFundWorker.exe' if self.frozen else 'pythonw.exe')
        self.interpreter = Path(interpreter or executable).resolve()
        arguments = ['--project-root', str(self.root)]
        if not self.frozen:
            arguments = ['-m', 'fundlab.scheduling.worker', *arguments]
        self.arguments = subprocess.list2cmdline(arguments)

    def call(self, operation, **extra):
        return transport(dict(operation=operation, name=self.name, source=self.source,
                              command=str(self.interpreter), arguments=self.arguments, directory=str(self.root), **extra))

    def query(self):
        return self.call('query')

    def xml(self, update_time, sid, *, enabled=True, now=None):
        from fundlab.scheduling.rules import beijing, slot_on
        from fundlab.scheduling.settings import validate_time
        now = beijing(now)
        start = slot_on(now.date(), validate_time(update_time))
        if start <= now:
            start += timedelta(days=1)
        root = ET.Element(f'{{{NS}}}Task', version='1.2')
        def node(parent, name, text=None, **attributes):
            element = ET.SubElement(parent, f'{{{NS}}}{name}', attributes)
            if text is not None:
                element.text = str(text)
            return element
        info = node(root, 'RegistrationInfo')
        node(info, 'Description', '集思录基金工具：交易日到时更新名单，登录/恢复后检查补跑；不保存密码、不唤醒电脑。')
        node(info, 'Source', self.source)
        triggers = node(root, 'Triggers')
        daily = node(triggers, 'CalendarTrigger', id='daily')
        node(daily, 'StartBoundary', start.isoformat())
        # Windows的StartWhenAvailable对有结束边界的时间任务生效。
        node(daily, 'EndBoundary', '2099-12-31T23:59:59+08:00')
        node(daily, 'Enabled', 'true')
        node(node(daily, 'ScheduleByDay'), 'DaysInterval', '1')
        logon = node(triggers, 'LogonTrigger', id='logon')
        node(logon, 'Enabled', 'true'); node(logon, 'Delay', 'PT30S'); node(logon, 'UserId', sid)
        unlock = node(triggers, 'SessionStateChangeTrigger', id='unlock')
        node(unlock, 'Enabled', 'true'); node(unlock, 'Delay', 'PT15S'); node(unlock, 'UserId', sid)
        node(unlock, 'StateChange', 'SessionUnlock')
        resume = node(triggers, 'EventTrigger', id='resume')
        node(resume, 'Enabled', 'true')
        node(resume, 'Subscription', "<QueryList><Query Id='0' Path='System'><Select Path='System'>*[System[Provider[@Name='Microsoft-Windows-Power-Troubleshooter'] and EventID=1]]</Select></Query></QueryList>")
        node(resume, 'Delay', 'PT30S')
        principal = node(node(root, 'Principals'), 'Principal', id='CurrentUser')
        node(principal, 'UserId', sid); node(principal, 'LogonType', 'InteractiveToken'); node(principal, 'RunLevel', 'LeastPrivilege')
        settings = node(root, 'Settings')
        for name, value in [('MultipleInstancesPolicy','IgnoreNew'), ('DisallowStartIfOnBatteries','false'),
                            ('StopIfGoingOnBatteries','false'), ('AllowHardTerminate','false'),
                            ('StartWhenAvailable','true'), ('RunOnlyIfNetworkAvailable','false'),
                            ('AllowStartOnDemand','true'), ('Enabled',str(enabled).lower()), ('Hidden','false'),
                            ('RunOnlyIfIdle','false'), ('WakeToRun','false'), ('ExecutionTimeLimit','PT0S'), ('Priority','7')]:
            node(settings, name, value)
        restart = node(settings, 'RestartOnFailure')
        node(restart, 'Interval', 'PT5M'); node(restart, 'Count', '2')
        action = node(node(root, 'Actions', Context='CurrentUser'), 'Exec')
        node(action, 'Command', str(self.interpreter)); node(action, 'Arguments', self.arguments)
        node(action, 'WorkingDirectory', str(self.root))
        return ET.tostring(root, encoding='unicode')

    def register(self, update_time, previous=None, *, enabled=True):
        previous = previous or self.query()
        if not self.interpreter.is_file():
            if self.frozen:
                raise FundlabError('WORKER_NOT_FOUND', '自动更新执行文件缺失，请恢复完整QFund程序目录后重试；原任务未修改。')
            raise FundlabError('PYTHON_NOT_FOUND', '项目无窗口Python入口缺失，请先恢复项目环境。')
        return self.call('register', xml=self.xml(update_time, previous['sid'], enabled=enabled))

    def state(self, update_time):
        raw = self.query()
        result = dict(installed=raw['installed'], enabled=False, healthy=False, name=self.name,
                      message='Windows定时任务尚未安装，不会到点自行执行。')
        if raw['installed']:
            root = ET.fromstring(raw['xml'])
            start = root.find(f'{{{NS}}}Triggers/{{{NS}}}CalendarTrigger/{{{NS}}}StartBoundary')
            from fundlab.scheduling.rules import parse_time, beijing, slot_on
            actual_time = parse_time(start.text).strftime('%H:%M') if start is not None else None
            # Windows会重排XML、移除默认值、将触发器SID转为用户名，按语义核对。
            def value(path, default=None):
                return root.findtext('/'.join(f'{{{NS}}}{part}' for part in path.split('/')), default)
            expected_settings = {'MultipleInstancesPolicy':'IgnoreNew', 'AllowHardTerminate':'false',
                                 'DisallowStartIfOnBatteries':'false', 'StopIfGoingOnBatteries':'false',
                                 'StartWhenAvailable':'true', 'WakeToRun':'false', 'ExecutionTimeLimit':'PT0S',
                                 'RestartOnFailure/Interval':'PT5M', 'RestartOnFailure/Count':'2'}
            defaults = {'WakeToRun':'false'}
            healthy = (actual_time == update_time and value('Principals/Principal/UserId') == raw['sid']
                       and parse_time(start.text) <= slot_on(beijing().date()+timedelta(days=1),update_time)
                       and value('Principals/Principal/LogonType') == 'InteractiveToken'
                       and value('Principals/Principal/RunLevel','LeastPrivilege') == 'LeastPrivilege'
                       and all(value('Settings/'+key,defaults.get(key)) == val for key,val in expected_settings.items())
                       and value('Settings/RunOnlyIfIdle','false') == 'false'
                       and value('Settings/RunOnlyIfNetworkAvailable','false') == 'false'
                       and len(root.findall(f'{{{NS}}}Actions/*')) == 1
                       and value('Actions/Exec/Command') == str(self.interpreter)
                       and value('Actions/Exec/Arguments') == self.arguments
                       and value('Actions/Exec/WorkingDirectory') == str(self.root))
            triggers = root.findall(f'{{{NS}}}Triggers/*')
            expected_triggers = ET.fromstring(self.xml(update_time, raw['sid'])).findall(f'{{{NS}}}Triggers/*')
            def signature(trigger):
                # 忽略每天起始日期和Windows自动解析的账户显示名；保留时间/订阅/启用等约束。
                def inner(child):
                    return (child.tag, (child.text or '').strip(), tuple(sorted(inner(grandchild) for grandchild in child)))
                return (trigger.tag, tuple(sorted(inner(child) for child in trigger
                        if child.tag not in {f'{{{NS}}}UserId', f'{{{NS}}}StartBoundary', f'{{{NS}}}Enabled'})))
            healthy = healthy and len(triggers)==4 and sorted(map(signature,triggers))==sorted(map(signature,expected_triggers))
            healthy = healthy and all(trigger.findtext(f'{{{NS}}}Enabled','true')=='true' for trigger in triggers)
            healthy = healthy and raw.get('trigger_users', [raw['sid'],raw['sid']]) == [raw['sid'],raw['sid']]
            result.update(enabled=raw['enabled'], healthy=healthy, actual_time=actual_time,
                          state=raw['state'], last_result=raw['last_result'], last_run=raw['last_run'], next_run=raw['next_run'],
                          message=('Windows定时任务已启用，按交易日和所选北京时间执行。' if raw['enabled'] else 'Windows定时任务已停用。') if healthy
                          else '系统任务与当前设置不一致，请点击安装/启用修复后再核对。')
        return result

    def change(self, action):
        if action not in {'install', 'disable', 'remove'}:
            raise FundlabError('VALIDATION_ERROR', '未知系统任务操作。')
        with ProfileLock(self.config.local_dir / 'scheduler.lock', FundlabError('SCHEDULE_BUSY', '到期更新正在执行，请等待结束后操作系统任务。')):
            from fundlab.scheduling.settings import ScheduleSettings
            value = ScheduleSettings(self.config).read()['update_time']
            previous = self.query()
            if action == 'install':
                self.register(value, previous, enabled=True)
            else:
                self.call(action)
            return self.state(value)


def system_task_status(config, update_time):
    try:
        return WindowsTask(config).state(update_time)
    except (FundlabError, ValueError, TypeError, KeyError, ET.ParseError) as error:
        return dict(installed=None, enabled=False, healthy=False, message=str(error), error_code=getattr(error, 'code', 'WINDOWS_TASK_INVALID'))
