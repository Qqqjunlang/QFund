"""手工样本仅在临时目录验证维护/分组/回收，不调用真实浏览器或任务。"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from fundlab.config import AppConfig
from fundlab.errors import FundlabError
from fundlab.parsers.history import parse_history
from fundlab.services.data_management import DataManagementService
from fundlab.services.fund_groups import FundGroupService
from fundlab.services.locking import update_lock
from fundlab.services.watchlist import WatchlistService
from fundlab.services.data_query import overview, history_page
from fundlab.services.update import update_one, retry_export
from fundlab.storage.repository import Repository, read_history_snapshot
from fundlab.storage import maintenance
from test_data_storage import FUND, STAMP, fixture_row


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = AppConfig(Path(self.temp.name))
        self.repo = Repository(self.config.database_path)
        self.run = str(uuid4())
        self.repo.start_run(self.run, FUND.code, 'initial')
        self.repo.commit_window(FUND, parse_history([fixture_row()], FUND.code, STAMP), self.run)
        self.service = DataManagementService(self.config)
        self.groups = FundGroupService(self.config)
        self.watchlist = WatchlistService(self.config)

    def revision(self):
        return self.service.state(FUND.code)['revision']

    def mutate(self, action='edit', day='2020-01-02', **changes):
        return self.service.mutate(FUND.code, day, action=action, changes=changes, expected_revision=self.revision())

    def lifecycle(self, action='trash', operation_id=None):
        return self.service.lifecycle(FUND.code, action=action, expected_revision=self.revision(), operation_id=operation_id)

    def group(self, name='关注'):
        return self.groups.change('create', name=name)['group_id']

    def members(self, group, codes):
        state = next(g for g in self.groups.state() if g['id'] == group)
        self.groups.change('members', group_id=group, codes=codes, expected_revision=state['revision'])

    def test_service_units_and_manual_add_protection(self):
        self.mutate(source_premium='12.5%')
        self.assertEqual(history_page(self.config, FUND.code)['rows'][0]['source_premium'], '12.5%')
        self.mutate('add', day='2020-01-03', close='3', nav='2', nav_date='2020-01-01')
        row = read_history_snapshot(self.config.database_path, FUND.code).rows[-1]
        self.assertEqual(row['effective_premium'], '0.5')
        self.assertEqual(row['protected_fields'], ['close','nav','nav_date'])
        self.assertEqual(row['record_origin'], 'manual')
        self.mutate('release', day='2020-01-03', close=None)
        self.assertIsNone(read_history_snapshot(self.config.database_path, FUND.code).rows[-1]['close'])

    def test_add_duplicate_deleted_and_invalid_are_noops(self):
        before = self.revision()
        for changes in ({'close':'NaN'},{'price_date':'2020-01-03'},{'close':'2','nav':'invalid'}):
            with self.assertRaises(FundlabError):
                self.mutate('add', day='2020-01-03', **changes)
        with self.assertRaises(FundlabError):
            self.mutate('add', close='5')
        self.assertEqual(self.revision(), before)
        self.mutate('delete')
        with self.assertRaises(FundlabError):
            self.mutate('add', close='5')

    def test_groups_many_to_many_and_delete_independent(self):
        self.watchlist.change('add', FUND.code)
        before = self.config.watchlist_path.read_bytes()
        a, b = self.group(), self.group('QDII')
        self.members(a, [FUND.code, FUND.code])
        self.members(b, [FUND.code])
        self.assertEqual([g['codes'] for g in self.groups.state()], [[FUND.code],[FUND.code]])
        self.groups.change('rename', group_id=a, name='  重点  ', expected_revision=2)
        self.groups.change('delete', group_id=a, expected_revision=3)
        self.assertEqual(self.config.watchlist_path.read_bytes(), before)
        self.assertEqual(len(read_history_snapshot(self.config.database_path, FUND.code).rows), 1)
        self.assertEqual(self.groups.state()[0]['codes'], [FUND.code])

    def test_invalid_group_and_stale_members_do_not_clear(self):
        a = self.group()
        self.members(a, [FUND.code])
        for name in ('', ' ', '关注', 'x'*81, '坏\n名字'):
            with self.assertRaises(FundlabError):
                self.group(name)
        with self.assertRaises(FundlabError):
            self.groups.change('members', group_id=a, codes=[], expected_revision=1)
        with self.assertRaises(FundlabError):
            self.groups.change('members', group_id=a, codes=['999999'], expected_revision=2)
        self.assertEqual(self.groups.state()[0]['codes'], [FUND.code])

    def test_trash_restore_keeps_protection_and_tombstones_not_membership(self):
        self.watchlist.change('add', FUND.code)
        a = self.group()
        self.members(a, [FUND.code])
        self.mutate(close='3')
        self.mutate('delete')
        exported = self.config.data_dir / 'funds' / 'old.csv'
        exported.parent.mkdir()
        exported.write_bytes(b'keep export')
        self.lifecycle()
        self.assertEqual(overview(self.config)['funds'], [])
        self.assertEqual(self.watchlist.state()['items'], [])
        self.assertEqual(self.groups.state()[0]['codes'], [])
        with self.assertRaises(FundlabError):
            history_page(self.config, FUND.code)
        self.lifecycle('restore')
        state = self.service.state(FUND.code)
        self.assertEqual(state['state'], 'active')
        self.assertEqual(state['deleted_dates'], ['2020-01-02'])
        self.assertEqual(state['protected'][0]['field'], 'close')
        self.assertEqual(self.watchlist.state()['items'], [])
        self.assertEqual(self.groups.state()[0]['codes'], [])
        self.assertEqual(exported.read_bytes(), b'keep export')
        self.mutate('restore')
        self.assertEqual(read_history_snapshot(self.config.database_path, FUND.code).rows[0]['close'], '3')

    def test_trash_retry_same_operation_is_idempotent(self):
        expected = self.revision()
        result = self.lifecycle(operation_id='same-op')
        again = self.service.lifecycle(FUND.code, action='trash', expected_revision=expected, operation_id='same-op')
        self.assertEqual(result, again)
        with self.assertRaises(FundlabError):
            self.service.lifecycle(FUND.code, action='restore', expected_revision=self.revision(), operation_id='same-op')

    def test_failure_before_watchlist_removal_blocks_all_normal_writes(self):
        self.watchlist.change('add', FUND.code)
        with patch.object(WatchlistService, '_remove_locked', side_effect=OSError('test fault')):
            with self.assertRaises(OSError):
                self.lifecycle()
        self.assertEqual(self.service.state(FUND.code)['state'], 'trashing')
        self.assertEqual(overview(self.config)['funds'], [])
        with self.assertRaises(FundlabError):
            self.repo.commit_window(FUND, parse_history([fixture_row(price='9')], FUND.code, STAMP), self.run)
        self.service.recover()
        self.assertEqual(self.service.state(FUND.code)['state'], 'trashed')
        self.assertEqual(self.watchlist.state()['items'], [])

    def test_failure_after_watchlist_removal_recovers_without_schedule_reset(self):
        self.watchlist.change('add', FUND.code)
        from fundlab.scheduling.store import ScheduleStore
        from fundlab.scheduling.settings import ScheduleSettings
        # 仅记录初始状态；维护不得创建或改写这两份文件。
        initial = (ScheduleStore(self.config).read(), ScheduleSettings(self.config).read())
        with patch('fundlab.storage.maintenance.finish_lifecycle', side_effect=OSError('test fault')):
            with self.assertRaises(OSError):
                self.lifecycle()
        self.assertEqual(self.watchlist.state()['items'], [])
        self.assertEqual(self.service.state(FUND.code)['state'], 'trashing')
        self.service.recover()
        self.assertEqual(initial, (ScheduleStore(self.config).read(), ScheduleSettings(self.config).read()))
        self.assertFalse(self.config.schedule_path.exists())

    def test_finalization_failure_rolls_back_groups_and_state(self):
        a = self.group()
        self.members(a, [FUND.code])
        with self.repo.connection() as db:
            db.execute("CREATE TRIGGER fault BEFORE INSERT ON mutation_events WHEN NEW.kind='trash' BEGIN SELECT RAISE(ABORT,'fault'); END")
        with self.assertRaises(sqlite3.Error):
            self.lifecycle()
        with self.repo.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM fund_group_members').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT state FROM fund_state').fetchone()[0], 'trashing')
            db.execute('DROP TRIGGER fault')
        self.service.recover()
        self.assertEqual(self.service.state(FUND.code)['state'], 'trashed')

    def test_restore_pending_does_not_become_visible_before_cleanup(self):
        self.lifecycle()
        with patch.object(WatchlistService, '_remove_locked', side_effect=OSError('fault')):
            with self.assertRaises(OSError):
                self.lifecycle('restore')
        self.assertEqual(self.service.state(FUND.code)['state'], 'restoring')
        self.assertEqual(overview(self.config)['funds'], [])
        self.service.recover()
        self.assertEqual(self.service.state(FUND.code)['state'], 'active')

    def test_shared_lock_blocks_maintenance_groups_but_allows_membership_change(self):
        with update_lock(self.config):
            for command in (lambda:self.mutate(close='3'),lambda:self.lifecycle(),lambda:self.group()):
                with self.assertRaises(FundlabError) as caught:
                    command()
                self.assertEqual(caught.exception.code, 'UPDATE_BUSY')
            self.watchlist.change('add',FUND.code)
            self.watchlist.change('set_enabled',FUND.code,False)
        self.assertFalse(self.watchlist.state()['items'][0]['enabled'])

    def test_membership_lock_failure_keeps_lifecycle_pending(self):
        from fundlab.services.locking import membership_lock
        self.watchlist.change('add', FUND.code)
        with membership_lock(self.config):
            with self.assertRaises(FundlabError) as caught:
                self.lifecycle()
            self.assertEqual(caught.exception.code, 'WATCHLIST_BUSY')
        self.assertEqual(self.service.state(FUND.code)['state'],'trashing')
        self.service.recover()
        self.assertEqual(self.watchlist.state()['items'],[])

    def test_trash_rejects_fetch_export_and_manual_write_before_io(self):
        self.lifecycle()
        with patch('fundlab.services.update.fetch_window', side_effect=AssertionError('no fetch')):
            for command in (lambda:update_one(self.config,FUND.code),lambda:retry_export(self.config,FUND.code),lambda:self.mutate(close='3'),lambda:self.watchlist.change('add',FUND.code)):
                with self.assertRaises(FundlabError):
                    command()

    def test_explicit_restore_collection_and_scheduled_never_restore(self):
        self.lifecycle()
        with patch('fundlab.services.update.fetch_window', side_effect=FundlabError('TEST_STOP','offline')) as fetch:
            with self.assertRaises(FundlabError):
                update_one(self.config, FUND.code, trigger='scheduled:test', restore_from_trash=True)
            self.assertEqual(fetch.call_count, 0)
            result = update_one(self.config, FUND.code, restore_from_trash=True)
            self.assertEqual(result.error_code, 'TEST_STOP')
        self.assertEqual(self.service.state(FUND.code)['state'], 'active')
        self.assertFalse(self.config.watchlist_path.exists())

    def test_empty_read_only_service_does_not_create_anything(self):
        empty = AppConfig(self.config.root/'absent')
        self.assertEqual(DataManagementService(empty).list_trash(), [])
        self.assertEqual(FundGroupService(empty).state(), [])
        self.assertFalse(empty.root.exists())

    def test_v2_upgrade_preserves_overrides_and_backup(self):
        self.mutate(close=None)
        with self.repo.connection() as db:
            for table in ('fund_group_members','fund_groups','maintenance_operations','fund_state'):
                db.execute('DROP TABLE '+table)
            db.execute('DELETE FROM schema_migrations WHERE version=3')
            db.execute('PRAGMA user_version=2')
        self.assertIsNone(read_history_snapshot(self.config.database_path,FUND.code).rows[0]['close'])
        Repository(self.config.database_path)
        backup, = self.config.data_dir.glob('*.schema2-*.bak')
        with closing(sqlite3.connect(backup)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT value_json FROM field_overrides').fetchone()[0], 'null')
        self.assertIsNone(read_history_snapshot(self.config.database_path,FUND.code).rows[0]['close'])

    def test_v2_to_v3_failure_rolls_back_and_retry_succeeds(self):
        with self.repo.connection() as db:
            for table in ('fund_group_members','fund_groups','maintenance_operations','fund_state'):
                db.execute('DROP TABLE '+table)
            db.execute('DELETE FROM schema_migrations WHERE version=3')
            db.execute('PRAGMA user_version=2')
        with patch('fundlab.storage.migrations.MAINTENANCE_TABLES', ('CREATE TABLE partial(x)','INVALID SQL')):
            with self.assertRaises(sqlite3.Error):
                Repository(self.config.database_path)
        with self.repo.connection() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],2)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='partial'").fetchone())
        Repository(self.config.database_path)

    def test_scheduler_stale_candidate_is_skipped_without_budget_consumption(self):
        from test_scheduling import ENROLLED, MONDAY
        from fundlab.scheduling.runner import run_due_updates
        from fundlab.scheduling.store import ScheduleStore
        with patch('fundlab.services.watchlist.utc_now', return_value=ENROLLED):
            self.watchlist.change('add', FUND.code)
        with patch('fundlab.scheduling.runner.update_one', side_effect=FundlabError('FUND_TRASHED','已回收')):
            run_due_updates(self.config, now=MONDAY)
        job = ScheduleStore(self.config).read()['latest'][FUND.code]
        self.assertEqual(job['status'], 'skipped')
        self.assertEqual(job['attempts'], 0)

    def test_collection_entry_obeys_field_protection(self):
        from fundlab.models import SourceWindow
        self.mutate(close='3')
        window = SourceWindow(FUND, [fixture_row(price='4', net_value='2')], STAMP, {'kind':'test_only'})
        with patch('fundlab.services.update.fetch_window', return_value=window):
            result = update_one(self.config, FUND.code)
        self.assertEqual(result.status, 'succeeded')
        row = read_history_snapshot(self.config.database_path, FUND.code).rows[0]
        self.assertEqual((row['close'],row['nav'],row['effective_premium']), ('3','2','0.5'))

    def test_manual_add_audit_failure_rolls_back_row_and_local_run(self):
        with self.repo.connection() as db:
            db.execute("CREATE TRIGGER fault_add BEFORE INSERT ON mutation_events WHEN NEW.kind='add' BEGIN SELECT RAISE(ABORT,'fault'); END")
            before = db.execute('SELECT count(*) FROM update_runs').fetchone()[0]
        with self.assertRaises(sqlite3.Error):
            self.mutate('add', day='2020-01-03', close='3')
        with self.repo.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM history').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM update_runs').fetchone()[0],before)

    def test_lifecycle_preserves_populated_schedule_budget_and_settings(self):
        from test_scheduling import MONDAY, ENROLLED
        from fundlab.scheduling.store import ScheduleStore
        from fundlab.scheduling.rules import build_plan
        with patch('fundlab.services.watchlist.utc_now', return_value=ENROLLED):
            self.watchlist.change('add', FUND.code)
        store = ScheduleStore(self.config)
        job = build_plan(self.watchlist.state()['items'], {}, MONDAY)[0]
        job.update(attempts=3, status='failed', error_code='AUTH_REQUIRED')
        store.plan([job])
        store.checked(MONDAY.isoformat(),'pending','fixture')
        settings = self.config.data_dir/'schedule-settings.json'
        settings.write_text(json.dumps({'schema':1,'update_time':'21:17','effective_from':ENROLLED}),encoding='utf-8')
        before = (self.config.schedule_path.read_bytes(), settings.read_bytes(), store.read())
        self.lifecycle()
        self.lifecycle('restore')
        self.assertEqual(before, (self.config.schedule_path.read_bytes(),settings.read_bytes(),store.read()))
