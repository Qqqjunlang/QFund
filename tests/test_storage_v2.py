"""临时数据库验证升级与手动保护；绝不连接本机行情。"""
from contextlib import closing
from pathlib import Path
import hashlib
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from fundlab.analysis.premium import premium_series
from fundlab.config import AppConfig
from fundlab.services.data_query import overview, history_page, download_history
from fundlab.errors import FundlabError
from fundlab.exports.service import render_csv
from fundlab.parsers.history import parse_history
from fundlab.storage.repository import Repository, read_history_snapshot
from test_data_storage import FUND, STAMP, fixture_row


class StorageV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'history.sqlite3'
        self.repo = Repository(self.path)
        self.commit()

    def commit(self, **values):
        run = str(uuid4())
        self.repo.start_run(run, FUND.code, 'incremental')
        return self.repo.commit_window(FUND, parse_history([fixture_row(**values)], FUND.code, STAMP), run)

    def row(self):
        return read_history_snapshot(self.path, FUND.code).rows[0]

    def mutate(self, action='edit', **changes):
        with self.repo.connection() as db:
            version = db.execute('SELECT revision FROM data_revisions').fetchone()[0]
        return self.repo.apply_mutation(FUND.code, '2020-01-02', changes=changes,
                                       action=action, expected_revision=version)

    def old_schema(self):
        # 退回旧版六表，构造已提交WAL中的旧格式资料。
        with self.repo.connection() as db:
            for table in ('fund_group_members', 'fund_groups', 'maintenance_operations', 'fund_state', 'field_overrides', 'deleted_dates', 'data_revisions', 'mutation_events'):
                db.execute('DROP TABLE ' + table)
            db.execute('DELETE FROM schema_migrations WHERE version>=2')
            db.execute('PRAGMA user_version=1')

    def test_upgrade_preserves_base_and_backup_including_wal(self):
        self.old_schema()
        # 保持连接打开，确保升级读取的是已提交但尚未checkpoint的WAL。
        with self.repo.connection() as db:
            db.execute("UPDATE history SET fetched_at='2020-01-04T00:00:00+00:00'")
            db.commit()
            original = tuple(db.execute('SELECT * FROM history').fetchone())
            self.assertTrue(Path(str(self.path) + '-wal').is_file())
            before = set(self.path.parent.glob('*.bak'))
            Repository(self.path)
        backup, = set(self.path.parent.glob('*.bak')) - before
        with closing(sqlite3.connect(backup)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertEqual(tuple(db.execute('SELECT * FROM history').fetchone()), original)
        with self.repo.connection() as db:
            self.assertEqual(tuple(db.execute('SELECT * FROM history').fetchone()), original)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        count = len(list(self.path.parent.glob('*.bak')))
        Repository(self.path)
        self.assertEqual(len(list(self.path.parent.glob('*.bak'))), count)

    def test_migration_fault_rolls_back_schema_and_version(self):
        self.old_schema()
        with patch('fundlab.storage.migrations.TABLES', ('CREATE TABLE partial(x)', 'INVALID SQL')):
            with self.assertRaises(sqlite3.Error):
                Repository(self.path)
        with self.repo.connection() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='partial'").fetchone())
            self.assertEqual(db.execute('SELECT count(*) FROM history').fetchone()[0], 1)
        Repository(self.path)

    def test_old_read_and_future_rejection_do_not_write(self):
        self.old_schema()
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.assertEqual(self.row()['close'], '2.345')
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        with self.repo.connection() as db:
            db.execute('PRAGMA user_version=99')
        before = self.path.read_bytes()
        with self.assertRaises(RuntimeError):
            Repository(self.path)
        with self.assertRaises(FundlabError):
            self.row()
        self.assertEqual(self.path.read_bytes(), before)

    def test_missing_database_read_does_not_create(self):
        absent = self.path.parent / 'absent.sqlite3'
        with self.assertRaises(FundlabError):
            read_history_snapshot(absent, FUND.code)
        self.assertFalse(absent.exists())

    def test_field_override_survives_collection_and_release_uses_latest_base(self):
        self.mutate(close='3')
        self.commit(price='4', net_value='2')
        row = self.row()
        self.assertEqual((row['close'], row['nav'], row['effective_premium']), ('3', '2', '0.5'))
        self.assertEqual(row['protected_fields'], ['close'])
        with self.repo.connection() as db:
            self.assertEqual(json.loads(db.execute('SELECT data_json FROM history').fetchone()[0])['close'], '4')
        self.mutate('release', close=None)
        self.assertEqual(self.row()['close'], '4')
        self.assertEqual(self.row()['effective_premium'], '0.1996')

    def test_null_zero_and_manual_premium_precedence(self):
        self.mutate(close=None)
        self.commit(price='3')
        self.assertIsNone(self.row()['close'])
        self.assertIsNone(self.row()['effective_premium'])
        self.mutate(close='0', nav='2')
        self.assertEqual(self.row()['effective_premium'], '-1')
        self.mutate(source_premium='0.125')
        self.assertEqual(self.row()['effective_premium'], '0.125')
        self.assertEqual(premium_series([self.row()]).points[0].premium, '12.500')
        self.assertIn('12.5%', render_csv([self.row()]).decode('utf-8-sig'))
        self.mutate(source_premium=None)
        self.assertIsNone(self.row()['effective_premium'])
        self.mutate('release', source_premium=None)
        self.mutate(nav='0')
        self.assertIsNone(self.row()['effective_premium'])

    def test_delete_blocks_collector_and_restore_retains_protection(self):
        self.mutate(close='3')
        self.mutate('delete')
        self.assertEqual(read_history_snapshot(self.path, FUND.code).rows, [])
        result = self.commit(price='8')
        self.assertEqual(result['skipped_deleted'], 1)
        self.mutate('restore')
        self.assertEqual(self.row()['close'], '3')
        self.mutate('release', close=None)
        self.assertEqual(self.row()['close'], '2.345')

    def test_stale_and_invalid_mutations_are_atomic(self):
        for changes in ({'close': 'NaN'}, {'close': True}, {'price_date': '2020-01-03'}, {'nav': '-1'}, {'nav_date': '2020-02-30'}, {'close': '3', 'nav': 'invalid'}):
            with self.subTest(changes=changes), self.assertRaises(FundlabError):
                self.mutate(**changes)
        with self.assertRaises(FundlabError):
            self.repo.apply_mutation(FUND.code, '2020-01-02', changes={'close': '9'}, expected_revision=0)
        self.assertEqual(self.row()['close'], '2.345')
        with self.repo.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM mutation_events').fetchone()[0], 0)

    def test_mutation_audit_and_write_failure_roll_back_together(self):
        with self.repo.connection() as db:
            db.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON mutation_events BEGIN SELECT RAISE(ABORT,'test fault'); END")
        with self.assertRaises(sqlite3.Error):
            self.mutate(close='3')
        self.assertEqual(self.row()['close'], '2.345')
        self.assertEqual(self.row()['protected_fields'], [])

    def test_query_analysis_and_exports_share_effective_values(self):
        from io import BytesIO
        from openpyxl import load_workbook
        config = AppConfig(Path(self.temp.name) / 'app')
        config.database_path.parent.mkdir(parents=True)
        with self.repo.connection() as source, closing(sqlite3.connect(config.database_path)) as target:
            source.backup(target)
        repo = Repository(config.database_path)
        repo.apply_mutation(FUND.code, '2020-01-02', changes={'close': '3', 'nav': '2'}, expected_revision=1)
        self.assertEqual(history_page(config, FUND.code)['rows'][0]['source_premium'], '50%')
        _, csv = download_history(config, FUND.code, 'csv')
        self.assertIn('50%', csv.decode('utf-8-sig'))
        _, xlsx = download_history(config, FUND.code, 'xlsx')
        book = load_workbook(BytesIO(xlsx))
        try:
            self.assertEqual(book['历史数据']['E2'].value, 0.5)
        finally:
            book.close()
        repo.apply_mutation(FUND.code, '2020-01-02', changes={}, action='delete', expected_revision=2)
        self.assertEqual(overview(config)['funds'][0]['records'], 0)
        self.assertEqual(history_page(config, FUND.code)['total'], 0)

    def test_existing_v1_read_does_not_upgrade(self):
        self.old_schema()
        self.assertEqual(len(read_history_snapshot(self.path, FUND.code).rows), 1)
        with self.repo.connection() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='field_overrides'").fetchone())

    def test_nonempty_unversioned_database_is_rejected(self):
        with self.repo.connection() as db:
            db.execute('PRAGMA user_version=0')
        before = self.path.read_bytes()
        with self.assertRaises(RuntimeError):
            Repository(self.path)
        self.assertEqual(self.path.read_bytes(), before)
