"""人工构造输入只供边界验证，不作为软件运行行情或默认缓存。"""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from fundlab.errors import FundlabError
from fundlab.models import Fund
from fundlab.parsers.history import parse_history
from fundlab.storage.repository import Repository

STAMP = "2020-01-03T00:00:00+00:00"
FUND = Fund("000001", "测试专用基金", "QDII", "https://example.invalid/fixture")


def fixture_row(day="2020-01-02", **overrides):
    cell = {"fund_id": "000001", "price_dt": day, "price": "2.345",
            "net_value_dt": "2020-01-01", "net_value": "1.9548", "discount_rt": "19.96",
            "volume": "46204.030", "amount": 968461, "amount_incr": 0,
            "amount_increase_rt": "0.000", "ref_increase_rt": "-2.50", "is_est": 0}
    cell.update(overrides)
    return {"id": day, "cell": cell}


class ParserTests(unittest.TestCase):
    def test_units_dates_zero_and_precision(self):
        record = parse_history([fixture_row()], "000001", STAMP)[0]
        self.assertEqual(record.source_premium, "0.1996")
        self.assertEqual(record.index_change, "-0.025")
        self.assertEqual(record.shares_change, "0")
        self.assertEqual(record.turnover, "46204.03")
        self.assertEqual(record.nav_date, "2020-01-01")
        self.assertEqual(record.raw["cell"]["volume"], "46204.030")

    def test_missing_is_not_zero_and_zero_nav_no_division(self):
        missing = parse_history([fixture_row(price="-", amount_incr="--")], "000001", STAMP)[0]
        self.assertIsNone(missing.close)
        self.assertIsNone(missing.shares_added)
        self.assertIsNone(missing.calculated_premium)
        zero = parse_history([fixture_row(net_value="0")], "000001", STAMP)[0]
        self.assertEqual(zero.nav, "0")
        self.assertIsNone(zero.calculated_premium)

    def test_invalid_rows_reject_batch(self):
        for field, bad in (("price", "NaN"), ("price", True), ("price", "-1"),
                           ("amount", "1,2"), ("price_dt", "2020-02-30"), ("fund_id", "999999")):
            with self.subTest(field=field), self.assertRaises(FundlabError):
                parse_history([fixture_row(), fixture_row("2020-01-03", **{field: bad})], "000001", STAMP)

    def test_duplicate_date_and_missing_field_rejected(self):
        with self.assertRaises(FundlabError):
            parse_history([fixture_row(), fixture_row()], "000001", STAMP)
        row = fixture_row()
        del row["cell"]["net_value"]
        with self.assertRaises(FundlabError):
            parse_history([row], "000001", STAMP)

    def test_format_changes_do_not_change_numeric_values(self):
        a = parse_history([fixture_row()], "000001", STAMP)[0]
        b = parse_history([fixture_row(price="2.3450", volume="46,204.030")], "000001", STAMP)[0]
        self.assertEqual(a.data(), b.data())


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Repository(Path(self.temp.name) / "funds.sqlite3")

    def commit(self, rows):
        run = str(uuid4())
        self.repo.start_run(run, FUND.code, "incremental")
        return self.repo.commit_window(FUND, parse_history(rows, FUND.code, STAMP), run)

    def test_repeat_update_is_idempotent(self):
        first = self.commit([fixture_row()])
        second = self.commit([fixture_row()])
        self.assertEqual((first["inserted"], second["inserted"], second["unchanged"], second["total"]), (1, 0, 1, 1))
        with self.repo.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM record_changes").fetchone()[0], 0)

    def test_new_window_keeps_older_records_and_logs_revision(self):
        self.commit([fixture_row("2020-01-01"), fixture_row("2020-01-02")])
        result = self.commit([fixture_row("2020-01-02", price="2.5"), fixture_row("2020-01-03")])
        self.assertEqual((result["inserted"], result["updated"], result["total"]), (1, 1, 3))
        self.assertEqual(self.repo.query_history(FUND.code)[-1]["price_date"], "2020-01-01")
        with self.repo.connection() as db:
            change = json.loads(db.execute("SELECT changes_json FROM record_changes").fetchone()[0])
        self.assertEqual(change["close"], {"old": "2.345", "new": "2.5"})

    def test_transaction_rolls_back_all_changes(self):
        self.commit([fixture_row()])
        run = str(uuid4())
        self.repo.start_run(run, FUND.code, "incremental")
        records = parse_history([fixture_row(price="2.5"), fixture_row("2020-01-03")], FUND.code, STAMP)
        records[1] = replace(records[1], raw={"invalid_json": {1, 2}})
        with self.assertRaises(TypeError):
            self.repo.commit_window(FUND, records, run)
        self.assertEqual(len(self.repo.query_history(FUND.code)), 1)
        self.assertEqual(self.repo.query_history(FUND.code)[0]["close"], "2.345")
        with self.repo.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM record_changes").fetchone()[0], 0)

    def test_reopen_retains_data_and_fund_code(self):
        self.commit([fixture_row()])
        reopened = Repository(self.repo.path)
        self.assertEqual(reopened.get_fund("000001").name, FUND.name)
        self.assertEqual(len(reopened.query_history("000001")), 1)
