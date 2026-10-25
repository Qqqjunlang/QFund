"""安装互斥、任务回滚和数据保留；不注册系统任务，不使用真实行情。"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fundlab.collectors.session import ProfileLock
from fundlab.config import AppConfig
from fundlab.errors import FundlabError
from fundlab.installation import InstallationGate, MaintenanceSession, maintenance_main


class FakeTask:
    def __init__(self, installed=True, enabled=True):
        self.original = dict(installed=installed, enabled=enabled, state=3, xml='original XML', sid='test')
        self.raw = deepcopy(self.original)
        self.calls = []

    def query(self):
        return deepcopy(self.raw)

    def state(self, clock):
        return {'healthy':True}

    def call(self, action, **values):
        self.calls.append((action, values))
        if action == 'disable': self.raw['enabled'] = False
        elif action == 'remove': self.raw['installed'] = False
        elif action == 'register': self.raw = deepcopy(self.original)
        return deepcopy(self.raw)


class InstallationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='QFund 安装 生命周期 ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = AppConfig(self.root / '用户 数据')
        self.app = self.root / '程序 目录'
        self.task = FakeTask()
        self.create_bundle()
        (self.app / 'qfund-install.json').write_text(json.dumps(dict(product='QFund', install_id='test',
            app_dir=str(self.app), data_root=str(self.config.root))), encoding='utf-8')
        self.sentinel = self.config.data_dir / 'keep.txt'
        self.sentinel.parent.mkdir(parents=True)
        self.sentinel.write_bytes(b'preserve user data')

    def create_bundle(self):
        self.app.mkdir(exist_ok=True)
        records = []
        for name in ('QFund.exe','QFundWorker.exe','_internal/payload.dat'):
            path = self.app / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'program fixture')
            records.append(dict(path=name, sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        (self.app / 'program-manifest.json').write_text(json.dumps(dict(schema=1,product='QFund',files=records)), encoding='utf-8')

    def session(self, mode='install'):
        return MaintenanceSession(self.config, self.app, 'test', mode, task_factory=lambda *a,**k:self.task)

    def test_running_instances_share_gate_but_exclude_installation(self):
        with InstallationGate(self.config), InstallationGate(self.config):
            with self.assertRaises(FundlabError):
                with InstallationGate(self.config, exclusive=True): pass

    def test_maintenance_excludes_all_new_runtime_instances(self):
        with InstallationGate(self.config, exclusive=True):
            with self.assertRaises(FundlabError):
                with InstallationGate(self.config): pass
        with InstallationGate(self.config): pass

    def test_upgrade_pauses_then_restores_enabled_task_and_keeps_data(self):
        with self.session() as session:
            self.assertFalse(self.task.raw['enabled'])
            session.commit()
        self.assertTrue(self.task.raw['enabled'])
        self.assertEqual([name for name,values in self.task.calls], ['disable','register'])
        self.assertEqual(self.sentinel.read_bytes(), b'preserve user data')

    def test_disabled_task_remains_disabled_after_upgrade(self):
        self.task = FakeTask(enabled=False)
        with self.session() as session: session.commit()
        self.assertFalse(self.task.raw['enabled'])
        self.assertEqual([name for name,values in self.task.calls], ['register'])

    def test_no_task_is_created_by_installation(self):
        self.task = FakeTask(installed=False, enabled=False)
        with self.session() as session: session.commit()
        self.assertEqual(self.task.calls, [])

    def test_cancel_restores_task_when_program_unchanged(self):
        with self.session() as session: session.rollback()
        self.assertTrue(self.task.raw['enabled'])

    def test_incomplete_upgrade_does_not_enable_changed_program(self):
        with self.session() as session:
            (self.app / '_internal/payload.dat').write_bytes(b'partial new program')
            with self.assertRaises(FundlabError) as caught: session.rollback()
            self.assertEqual(caught.exception.code, 'INSTALL_ROLLBACK_UNCONFIRMED')
        self.assertFalse(self.task.raw['enabled'])
        self.assertEqual(self.sentinel.read_bytes(), b'preserve user data')

    def test_corrupted_new_bundle_cannot_commit(self):
        with self.session() as session:
            (self.app / 'QFundWorker.exe').write_bytes(b'corrupted')
            with self.assertRaises(FundlabError): session.commit()
        self.assertFalse(self.task.raw['enabled'])

    def test_uninstall_removes_only_owned_task_and_preserves_data(self):
        with self.session('uninstall') as session:
            session.remove_task(); session.commit()
        self.assertFalse(self.task.raw['installed'])
        self.assertEqual(self.sentinel.read_bytes(), b'preserve user data')

    def test_uninstall_cannot_commit_before_task_removal(self):
        with self.session('uninstall') as session:
            with self.assertRaises(FundlabError): session.commit()
            session.rollback()
        self.assertTrue(self.task.raw['enabled'])

    def test_legacy_running_web_blocks_before_task_mutation(self):
        with ProfileLock(self.config.local_dir / 'web-server.lock'):
            with self.assertRaises(FundlabError):
                with self.session(): pass
        self.assertEqual(self.task.calls, [])
        with InstallationGate(self.config): pass

    def test_running_worker_blocks_before_task_mutation(self):
        self.task.raw['state'] = 4
        with self.assertRaises(FundlabError):
            with self.session(): pass
        self.assertEqual(self.task.calls, [])

    def test_foreign_marker_does_not_modify_task(self):
        marker = self.app / 'qfund-install.json'
        data = json.loads(marker.read_text()); data['install_id']='other'; marker.write_text(json.dumps(data))
        with self.assertRaises(FundlabError):
            with self.session(): pass
        self.assertEqual(self.task.calls, [])

    def test_unmanaged_target_does_not_get_overwritten(self):
        (self.app / 'qfund-install.json').unlink()
        with self.assertRaises(FundlabError):
            with self.session(): pass
        self.assertEqual(self.task.calls, [])

    def test_program_and_user_data_cannot_overlap(self):
        with self.assertRaises(FundlabError):
            with MaintenanceSession(self.config, self.config.root / 'Programs', 'test', 'install', task_factory=lambda *a,**k:self.task): pass
        self.assertEqual(self.task.calls, [])

    def test_maintenance_channel_cancel_releases_gate_and_restores_task(self):
        channel = self.root / 'ready.ini'
        result = []
        with patch('fundlab.scheduling.windows_task.WindowsTask', return_value=self.task):
            thread = threading.Thread(target=lambda:result.append(maintenance_main(self.config, self.app, 'test', 'install', channel, timeout=5)))
            thread.start()
            for _ in range(100):
                if channel.exists() and 'state=ready' in channel.read_text(encoding='utf-16'): break
                time.sleep(.01)
            else: self.fail('maintenance did not become ready')
            channel.with_suffix('.command').write_text('rollback', encoding='utf-8')
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result,[0])
        self.assertTrue(self.task.raw['enabled'])
        with InstallationGate(self.config): pass


if __name__ == '__main__': unittest.main()
