"""安装互斥、任务回滚和数据保留；不注册系统任务，不使用真实行情。"""
from copy import deepcopy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import tempfile
import sqlite3
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
        metadata = patch('fundlab.installation.package_version', return_value='1.0.0')
        metadata.start()
        self.addCleanup(metadata.stop)
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
        (self.app / 'program-manifest.json').write_text(json.dumps(dict(schema=1,product='QFund',version='1.0.0',files=records)), encoding='utf-8')

    def session(self, mode='install', incoming_version=None):
        return MaintenanceSession(self.config, self.app, 'test', mode, task_factory=lambda *a,**k:self.task,
                                  incoming_version=incoming_version)

    def set_version(self, version):
        path = self.app / 'program-manifest.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        data['version'] = version
        path.write_text(json.dumps(data), encoding='utf-8')

    def test_version_comparison_rejects_downgrade_before_task_query(self):
        self.set_version('1.10.0')
        before = {path.name: path.read_bytes() for path in self.app.iterdir() if path.is_file()}
        with patch.object(self.task, 'query') as query:
            with self.assertRaises(FundlabError) as caught:
                with self.session(incoming_version='1.9.0'): pass
            query.assert_not_called()
        self.assertEqual(caught.exception.code, 'INSTALL_DOWNGRADE_BLOCKED')
        self.assertEqual(self.task.calls, [])
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.app.iterdir() if path.is_file()})
        with InstallationGate(self.config): pass

    def test_upgrade_and_same_version_repair_record_version(self):
        for installed, incoming in (('1.9.0', '1.10.0'), ('1.10.0', '1.10.0'), ('1.10.0rc1', '1.10.0')):
            with self.subTest(installed=installed):
                marker = self.app / 'qfund-install.json'
                data = json.loads(marker.read_text(encoding='utf-8'))
                data.pop('program_version', None)
                marker.write_text(json.dumps(data), encoding='utf-8')
                self.set_version(installed)
                with self.session(incoming_version=incoming) as session:
                    self.set_version(incoming)
                    session.commit()
                self.assertEqual(json.loads(marker.read_text(encoding='utf-8'))['program_version'], incoming)

    def test_unknown_legacy_version_stops_but_recorded_version_allows_manifest_repair(self):
        manifest = self.app / 'program-manifest.json'
        manifest.write_text('invalid json', encoding='utf-8')
        with self.assertRaises(FundlabError) as caught:
            with self.session(): pass
        self.assertEqual(caught.exception.code, 'INSTALL_VERSION_UNCONFIRMED')
        self.assertEqual(self.task.calls, [])
        marker = self.app / 'qfund-install.json'
        data = json.loads(marker.read_text(encoding='utf-8'))
        data['program_version'] = '1.0.0'
        marker.write_text(json.dumps(data), encoding='utf-8')
        with self.session(incoming_version='1.0.0') as session:
            self.create_bundle()
            session.commit()
        self.assertTrue(self.task.raw['enabled'])

    def test_recorded_newer_version_blocks_repair_with_older_program(self):
        marker = self.app / 'qfund-install.json'
        data = json.loads(marker.read_text(encoding='utf-8'))
        data['program_version'] = '2.0.0'
        marker.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(FundlabError) as caught:
            with self.session(incoming_version='1.0.0'): pass
        self.assertEqual(caught.exception.code, 'INSTALL_DOWNGRADE_BLOCKED')
        self.assertEqual(self.task.calls, [])

    def test_invalid_incoming_version_and_commit_mismatch_are_rejected(self):
        for value in ('', 'unknown', 1):
            with self.subTest(value=value), self.assertRaises(FundlabError):
                with self.session(incoming_version=value): pass
        self.assertEqual(self.task.calls, [])
        with self.session(incoming_version='1.1.0') as session:
            with self.assertRaises(FundlabError) as caught: session.commit()
            self.assertEqual(caught.exception.code, 'INSTALL_VERSION_MISMATCH')
            session.rollback()
        self.assertTrue(self.task.raw['enabled'])

    def test_unsupported_or_corrupt_database_rejected_without_task_query(self):
        for path in (self.config.database_path, self.config.watchlist_path, self.config.schedule_path):
            for content in ('future', 'unversioned', 'corrupt'):
                with self.subTest(path=path.name, content=content):
                    if content == 'corrupt':
                        path.write_bytes(b'invalid database')
                    else:
                        with closing(sqlite3.connect(path, isolation_level=None)) as db:
                            db.execute('CREATE TABLE fixture(value)')
                            db.execute('PRAGMA user_version=' + ('99' if content == 'future' else '0'))
                    before = path.read_bytes()
                    with patch.object(self.task, 'query') as query:
                        with self.assertRaises(FundlabError):
                            with self.session(): pass
                        query.assert_not_called()
                    self.assertEqual(path.read_bytes(), before)
                    self.assertEqual(self.task.calls, [])
                    path.unlink()
        with InstallationGate(self.config): pass

    def test_installation_reads_old_schema_without_migration(self):
        for schema in (0, 1, 2, 3):
            path = self.config.database_path
            with closing(sqlite3.connect(path, isolation_level=None)) as db:
                if schema:
                    db.execute('CREATE TABLE fixture(value)')
                db.execute('PRAGMA user_version=' + str(schema))
            before = path.read_bytes()
            with self.session() as session: session.commit()
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(path.parent.glob('*.bak')), [])
            path.unlink()

    def test_uninstall_does_not_require_readable_business_data_or_program_version(self):
        self.config.database_path.write_bytes(b'corrupt data to preserve')
        settings = self.config.data_dir / 'schedule-settings.json'
        settings.write_bytes(b'corrupt settings to preserve')
        self.set_version('unknown')
        self.task = FakeTask(installed=False, enabled=False)
        with self.session('uninstall') as session:
            session.remove_task(); session.commit()
        self.assertEqual(self.config.database_path.read_bytes(), b'corrupt data to preserve')
        self.assertEqual(settings.read_bytes(), b'corrupt settings to preserve')
        settings.unlink()
        self.task = FakeTask(installed=True, enabled=True)
        with self.session('uninstall') as session:
            session.remove_task(); session.commit()
        self.assertFalse(self.task.raw['installed'])
        self.assertEqual(self.config.database_path.read_bytes(), b'corrupt data to preserve')

    def test_invalid_time_settings_block_installation_before_task_query(self):
        path = self.config.data_dir / 'schedule-settings.json'
        path.write_bytes(b'{}')
        with patch.object(self.task, 'query') as query:
            with self.assertRaises(FundlabError):
                with self.session(): pass
            query.assert_not_called()
        self.assertEqual(path.read_bytes(), b'{}')

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
