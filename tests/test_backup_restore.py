"""备份/WAL/恢复/失败还原检查；全部使用临时数据及系统任务替身。"""
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fundlab.config import AppConfig
from fundlab.errors import FundlabError
from fundlab.parsers.history import parse_history
from fundlab.services.update import update_lock
from fundlab.storage.repository import Repository
from test_data_storage import FUND, STAMP, fixture_row

spec=importlib.util.spec_from_file_location('backup_verify',Path(__file__).resolve().parents[1]/'scripts/backup_verify.py')
backup_module=importlib.util.module_from_spec(spec); spec.loader.exec_module(backup_module)


class FakeTask:
    def __init__(self,enabled=True): self.enabled=enabled; self.original=enabled; self.calls=[]
    def state(self,clock): return {'healthy':True}
    def query(self): return {'installed':True,'enabled':self.enabled,'state':3,'xml':'original-enabled' if self.enabled else 'original-disabled'}
    def call(self,operation,**args):
        self.calls.append((operation,args))
        if operation=='disable': self.enabled=False
        elif operation=='register': self.enabled=args['xml']=='original-enabled'
        return self.query()


class BackupRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.config=AppConfig(Path(self.temp.name)); self.task=FakeTask()
        repo=Repository(self.config.database_path)
        repo.start_run('seed',FUND.code,'initial')
        repo.commit_window(FUND,parse_history([fixture_row()],FUND.code,STAMP),'seed')
        repo.finish_run({'run_id':'seed','status':'succeeded','data_saved':True})
        (self.config.root/'README.md').write_text('Test source',encoding='utf-8')
        folder=self.config.funds_dir/'old_archive'
        folder.mkdir(parents=True); (folder/'retained.csv').write_bytes(b'original archive')
        self.config.profile_dir.mkdir(parents=True); (self.config.profile_dir/'test-private-profile.txt').write_text('TEST ONLY PRIVATE SENTINEL')

    def run_backup(self): return backup_module.backup_and_verify(self.config,task=self.task)

    def test_real_sqlite_backup_and_isolated_services_match_and_profile_excluded(self):
        result=self.run_backup(); restored=Path(result['restored'])
        self.assertEqual(result['status'],'verified')
        self.assertTrue(result['restored_business_matches']); self.assertTrue(result['production_data_unchanged'])
        self.assertTrue(self.task.enabled)
        self.assertEqual((restored/'data/funds/old_archive/retained.csv').read_bytes(),b'original archive')
        self.assertFalse((restored/'.local/edge-profile').exists())
        self.assertFalse((restored/'.venv').exists())
        self.assertEqual(result['analysis_funds'],1)
        self.assertFalse(result['restored_task_registered']); self.assertFalse(result['network_used'])
        self.assertEqual(json.loads((Path(result['backup'])/'manifest.json').read_text(encoding='utf-8'))['status'],'verified')

    def test_uncheckpointed_wal_record_is_present_in_backup(self):
        with closing(sqlite3.connect(self.config.database_path)) as db:
            db.execute('PRAGMA wal_autocheckpoint=0')
            db.execute("UPDATE update_runs SET trigger='WAL_ONLY_MARKER' WHERE run_id='seed'"); db.commit()
            self.assertTrue(Path(str(self.config.database_path)+'-wal').exists())
            result=self.run_backup()
            with closing(sqlite3.connect(Path(result['backup'])/'payload/data/fundlab.sqlite3')) as saved:
                self.assertEqual(saved.execute("SELECT trigger FROM update_runs WHERE run_id='seed'").fetchone()[0],'WAL_ONLY_MARKER')

    def test_copy_failure_preserves_partial_backup_and_restores_enabled_task(self):
        original=backup_module.database_summary(self.config.database_path)
        with patch.object(backup_module.shutil,'copyfile',side_effect=OSError('test copy failure')),self.assertRaises(OSError):
            self.run_backup()
        self.assertTrue(self.task.enabled)
        self.assertEqual(backup_module.database_summary(self.config.database_path),original)
        manifests=list((self.config.root/'backups').glob('*/manifest.json'))
        self.assertEqual(len(manifests),1)
        self.assertEqual(json.loads(manifests[0].read_text(encoding='utf-8'))['status'],'incomplete')

    def test_disabled_task_is_not_enabled_by_backup(self):
        self.task=FakeTask(False)
        result=self.run_backup()
        self.assertFalse(self.task.enabled); self.assertEqual(self.task.calls,[])
        self.assertTrue(result['system_task_state_restored'])

    def test_busy_update_does_not_pause_task_or_create_backup(self):
        with update_lock(self.config),self.assertRaises(FundlabError): self.run_backup()
        self.assertEqual(self.task.calls,[])
        self.assertFalse((self.config.root/'backups').exists())

    def test_active_web_task_prevents_backup(self):
        (self.config.local_dir/'web-tasks.json').write_text('[{"status":"queued"}]',encoding='utf-8')
        with self.assertRaises(FundlabError) as caught: self.run_backup()
        self.assertEqual(caught.exception.code,'BACKUP_BUSY')
        self.assertEqual(self.task.calls,[])

    def test_repeated_runs_create_new_directories_not_overwrite_previous(self):
        first=self.run_backup(); saved=Path(first['backup'])/'manifest.json'; data=saved.read_bytes()
        second=self.run_backup()
        self.assertNotEqual(first['backup'],second['backup'])
        self.assertEqual(saved.read_bytes(),data)

    def test_source_file_change_rejects_backup_and_restores_task(self):
        original=backup_module.shutil.copyfile
        def change_source(source,target):
            value=original(source,target)
            if Path(source).name=='README.md': Path(source).write_text('changed concurrently')
            return value
        with patch.object(backup_module.shutil,'copyfile',side_effect=change_source),self.assertRaises(FundlabError) as caught:
            self.run_backup()
        self.assertEqual(caught.exception.code,'BACKUP_SOURCE_CHANGED')
        self.assertTrue(self.task.enabled)

    def test_task_restore_failure_retains_pre_pause_checkpoint_and_incomplete_manifest(self):
        original_call=self.task.call
        def restore_error(operation,**args):
            if operation=='register': raise FundlabError('WINDOWS_TASK_FAILED','test restore failure')
            checkpoints=list((self.config.root/'backups').glob('*/operation-start.json'))
            self.assertEqual(len(checkpoints),1)
            self.assertTrue(json.loads(checkpoints[0].read_text(encoding='utf-8'))['system_task_before']['enabled'])
            return original_call(operation,**args)
        with patch.object(self.task,'call',side_effect=restore_error),self.assertRaises(FundlabError): self.run_backup()
        manifest=json.loads(next((self.config.root/'backups').glob('*/manifest.json')).read_text(encoding='utf-8'))
        self.assertEqual(manifest['status'],'incomplete')
        self.assertTrue(manifest['system_task_before']['enabled'])
        self.assertFalse(self.task.enabled)


if __name__=='__main__': unittest.main()
