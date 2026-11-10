"""Verify the import service with isolated in-memory files and temporary databases."""

from datetime import date
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from openpyxl import Workbook

from fundlab.config import AppConfig
from fundlab.errors import FundlabError
from fundlab.parsers.history import parse_history
from fundlab.services.data_import import DataImportService
from fundlab.services.data_management import DataManagementService
from fundlab.storage.repository import Repository, read_history_snapshot
from test_data_storage import FUND, STAMP, fixture_row


class ImportTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = AppConfig(Path(directory.name))
        self.importer = DataImportService(self.config)
        self.maintenance = DataManagementService(self.config)

    def preview(self, rows, **options):
        return self.importer.preview('000001', '测试基金', 'file.csv',
                                     ('价格日期,收盘价,溢价率\n' + rows).encode('utf-8-sig'), **options)

    def snapshot(self):
        return read_history_snapshot(self.config.database_path, '000001')

    def test_new_csv_is_read_only_until_commit_and_receipt_survives_restart(self):
        preview = self.preview('2020-01-02,2,1.5%\n2020-01-03,0,\n')
        self.assertFalse(self.config.database_path.exists())
        preview['sample'][0]['values']['close'] = '99'
        result = self.importer.commit(preview['token'])
        self.assertEqual(result['inserted'], 2)
        self.assertEqual(DataImportService(self.config).commit(preview['token']), result)
        snapshot = self.snapshot()
        self.assertEqual(snapshot.fund.code, '000001')
        self.assertEqual(snapshot.fund.source_url, '')
        first, second = snapshot.rows
        self.assertEqual((first['close'], first['effective_premium'], first['record_origin']), ('2', '0.015', 'import'))
        self.assertEqual((second['close'], second['effective_premium']), ('0', None))
        self.assertEqual(first['protected_fields'], [])

    def test_explicit_mapping_gb18030_and_ratio_fraction(self):
        preview = self.importer.preview('000001', '测试基金', 'file.csv',
            '数值,日期,净值日,比例\n2,2020-01-02,2020-01-01,0.0125\n'.encode('gb18030'),
            encoding='gb18030', mapping={'close': 1, 'price_date': 2, 'nav_date': 3, 'source_premium': 4},
            ratio_unit='fraction')
        self.assertEqual(preview['error_count'], 0)
        self.importer.commit(preview['token'])
        self.assertEqual(self.snapshot().rows[0]['effective_premium'], '0.0125')
        self.assertEqual(self.snapshot().rows[0]['nav_date'], '2020-01-01')

    def test_xlsx_sheet_header_excel_date_and_numeric_percent(self):
        book = Workbook()
        tab = book.create_sheet('行情')
        tab.append(['导入示例'])
        tab.append(['价格日期', '溢价率'])
        tab.append([date(2020, 1, 2), 0.0125])
        tab['B3'].number_format = '0.00%'
        stream = BytesIO()
        book.save(stream)
        book.close()
        preview = self.importer.preview('000001', '测试基金', 'file.xlsx', stream.getvalue(),
                                       sheet='行情', header_row=2)
        self.assertIn('行情', preview['options']['sheets'])
        self.importer.commit(preview['token'])
        self.assertEqual(self.snapshot().rows[0]['effective_premium'], '0.0125')

    def test_formula_in_unmapped_column_blocks_entire_workbook(self):
        book = Workbook()
        book.active.append(['价格日期', '收盘价', '无关列'])
        book.active.append(['2020-01-02', 2, '=2+2'])
        stream = BytesIO()
        book.save(stream)
        book.close()
        preview = self.importer.preview('000001', '测试基金', 'file.xlsx', stream.getvalue())
        self.assertEqual(preview['errors'][0]['row'], 2)
        self.assertIsNone(preview['token'])
        self.assertFalse(self.config.database_path.exists())

    def test_invalid_and_duplicate_rows_have_no_confirmation_token(self):
        preview = self.preview('2020-01-02,2,1\n2020-01-02,3,2\n2020-02-30,NaN,0\n')
        self.assertEqual(preview['error_count'], 3)
        self.assertEqual({item['row'] for item in preview['errors']}, {3, 4})
        self.assertIsNone(preview['token'])
        self.assertFalse(self.config.database_path.exists())

    def test_mismatched_fund_codes_block_the_entire_file(self):
        content = '基金代码,价格日期,收盘价\n000001,2020-01-02,2\n000002,2020-01-03,3\n'
        preview = self.importer.preview('000001', '测试基金', 'file.csv', content.encode())
        self.assertEqual(preview['errors'][0]['row'], 3)
        self.assertEqual(preview['errors'][0]['field'], 'code')
        self.assertIsNone(preview['token'])
        self.assertFalse(self.config.database_path.exists())

    def test_preview_stale_and_existing_or_deleted_rows_are_skipped(self):
        original = self.preview('2020-01-02,2,1\n2020-01-03,3,1\n')
        self.importer.commit(original['token'])
        pending = self.preview('2020-01-02,8,0\n2020-01-03,8,0\n2020-01-04,4,0\n')
        self.maintenance.mutate('000001', '2020-01-03', action='delete',
                                expected_revision=self.maintenance.state('000001')['revision'])
        with self.assertRaises(FundlabError) as failure:
            self.importer.commit(pending['token'])
        self.assertEqual(failure.exception.code, 'PREVIEW_STALE')
        updated = self.preview('2020-01-02,8,0\n2020-01-03,8,0\n2020-01-04,4,0\n')
        result = self.importer.commit(updated['token'])
        self.assertEqual((result['inserted'], result['skipped_existing'], result['skipped_deleted']), (1, 1, 1))
        self.assertEqual([row['close'] for row in self.snapshot().rows], ['2', '4'])

    def test_restore_requires_confirmation_and_batch_failure_rolls_back_restore(self):
        self.importer.commit(self.preview('2020-01-02,2,1\n')['token'])
        self.maintenance.lifecycle('000001', action='trash',
                                   expected_revision=self.maintenance.state('000001')['revision'])
        preview = self.preview('2020-01-03,3,1\n2020-01-04,4,1\n')
        self.assertTrue(preview['requires_restore'])
        with self.assertRaises(FundlabError):
            self.importer.commit(preview['token'])
        repo = Repository(self.config.database_path)
        with repo.connection() as db:
            db.execute("""CREATE TRIGGER reject_last BEFORE INSERT ON history WHEN NEW.price_date='2020-01-04'
                          BEGIN SELECT RAISE(ABORT,'fixture'); END""")
        with self.assertRaises(FundlabError) as failure:
            self.importer.commit(preview['token'], restore=True)
        self.assertEqual(failure.exception.code, 'IMPORT_WRITE_FAILED')
        self.assertEqual(self.maintenance.state('000001')['state'], 'trashed')
        with repo.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM history').fetchone()[0], 1)
            db.execute('DROP TRIGGER reject_last')
        result = self.importer.commit(preview['token'], restore=True)
        self.assertTrue(result['restored'])
        self.assertEqual(len(self.snapshot().rows), 3)

    def test_collector_can_replace_imported_values_and_provenance(self):
        self.importer.commit(self.preview('2020-01-02,9,1\n')['token'])
        repo = Repository(self.config.database_path)
        run = str(uuid4())
        repo.start_run(run, FUND.code, 'incremental')
        repo.commit_window(FUND, parse_history([fixture_row()], FUND.code, STAMP), run)
        row = self.snapshot().rows[0]
        self.assertNotEqual(row['close'], '9')
        self.assertEqual(row['record_origin'], 'jisilu')

    def test_expired_preview_and_file_limits(self):
        preview = self.preview('2020-01-02,2,1\n')
        with patch('fundlab.services.data_import.PREVIEW_TTL', 0):
            with self.assertRaises(FundlabError) as failure:
                self.importer.commit(preview['token'])
        self.assertEqual(failure.exception.code, 'PREVIEW_STALE')
        with patch('fundlab.parsers.tabular.MAX_ROWS', 1):
            with self.assertRaises(FundlabError) as failure:
                self.preview('2020-01-02,2,1\n2020-01-03,3,1\n')
        self.assertEqual(failure.exception.code, 'IMPORT_FILE_SIZE')
        self.assertFalse(self.config.database_path.exists())
