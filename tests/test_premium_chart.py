"""交互图第一步：测试数据只用于核算单位、缺失和本地读取边界。"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import io
import json
from contextlib import redirect_stdout, redirect_stderr
from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fundlab.analysis.charts import render_premium_timeline
from fundlab.analysis.premium import premium_series
from fundlab.config import AppConfig
from fundlab.errors import FundlabError
from fundlab.models import Fund
from fundlab.parsers.history import parse_history
from fundlab.services.analysis import analyze_one, create_premium_chart
from fundlab.analysis.calculations import AnalysisRequest
from fundlab.cli import main
from fundlab.storage.repository import Repository, read_history_snapshot
from test_data_storage import fixture_row, STAMP


class ScriptInventory(HTMLParser):
    def __init__(self):
        super().__init__()
        self.external_scripts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("src"):
            self.external_scripts.append(dict(attrs)["src"])


class PremiumSeriesTests(unittest.TestCase):
    def test_units_sorting_missing_and_source_preference(self):
        rows = [{"price_date": "2020-01-03", "source_premium": None, "calculated_premium": "0.99"},
                {"price_date": "2020-01-02", "source_premium": "0.0125"},
                {"price_date": "2020-01-01", "source_premium": "-0.002"}]
        original = deepcopy(rows)
        series = premium_series(rows)
        self.assertEqual([p.price_date for p in series.points], ["2020-01-01", "2020-01-02", "2020-01-03"])
        self.assertEqual([p.premium for p in series.points], ["-0.200", "1.2500", None])
        self.assertEqual(series.latest_valid.price_date, "2020-01-02")
        self.assertEqual(rows, original)

    def test_zero_is_valid_and_precision_is_preserved(self):
        series = premium_series([{"price_date": "2020-01-01", "source_premium": "0"},
                                 {"price_date": "2020-01-02", "source_premium": "0.012345678901234567890123456789012345"}])
        self.assertEqual(series.points[0].premium, "0")
        self.assertEqual(series.points[1].premium, "1.234567890123456789012345678901234500")

    def test_all_missing_has_no_current_value(self):
        self.assertIsNone(premium_series([{"price_date": "2020-01-01", "source_premium": None}]).latest_valid)

    def test_absent_or_broken_input_is_not_repaired(self):
        with self.assertRaises(FundlabError) as caught:
            premium_series([])
        self.assertEqual(caught.exception.code, "NO_DATA")
        for value in ("NaN", "Infinity", "invalid"):
            with self.subTest(value=value), self.assertRaises(FundlabError) as caught:
                premium_series([{"price_date": "2020-01-01", "source_premium": value}])
            self.assertEqual(caught.exception.code, "DATA_CONTRACT_ERROR")


class LocalChartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="基金图表 测试 ")
        self.addCleanup(self.temp.cleanup)
        self.config = AppConfig(Path(self.temp.name))
        self.fund = Fund("000001", "测试基金", "QDII", "https://example.invalid/test")
        self.repo = Repository(self.config.database_path)
        self.repo.start_run("chart-test", self.fund.code, "initial")
        self.repo.commit_window(self.fund, parse_history([fixture_row("2020-01-01"), fixture_row("2020-01-02", discount_rt="-")],
                                                        self.fund.code, STAMP), "chart-test", {"full_history_verified": False})

    def test_snapshot_is_read_only_and_retains_all_local_rows(self):
        before = hashlib.sha256(self.config.database_path.read_bytes()).hexdigest()
        with patch.object(Repository, "__init__", side_effect=AssertionError("analysis must not initialise database")):
            snapshot = read_history_snapshot(self.config.database_path, self.fund.code)
        self.assertEqual(snapshot.fund, self.fund)
        self.assertEqual(len(snapshot.rows), 2)
        self.assertEqual(snapshot.rows[0]["price_date"], "2020-01-01")
        self.assertFalse(snapshot.coverage["full_history_verified"])
        self.assertEqual(hashlib.sha256(self.config.database_path.read_bytes()).hexdigest(), before)

    def test_missing_database_and_fund_do_not_create_data(self):
        missing = self.config.root / "absent" / "funds.sqlite3"
        with self.assertRaises(FundlabError):
            read_history_snapshot(missing, self.fund.code)
        self.assertFalse(missing.parent.exists())
        with self.assertRaises(FundlabError) as caught:
            read_history_snapshot(self.config.database_path, "999999")
        self.assertEqual(caught.exception.code, "NO_DATA")

    def test_unsupported_database_version_is_read_only(self):
        with self.repo.connection() as db:
            db.execute("PRAGMA user_version=2")
        before = self.config.database_path.read_bytes()
        with self.assertRaises(FundlabError) as caught:
            read_history_snapshot(self.config.database_path, self.fund.code)
        self.assertEqual(caught.exception.code, "DATABASE_VERSION")
        self.assertEqual(self.config.database_path.read_bytes(), before)

    def test_output_is_offline_and_does_not_overwrite_history(self):
        directory = self.config.funds_dir / "000001_测试基金"
        directory.mkdir(parents=True)
        history = directory / "history.csv"
        history.write_bytes(b"saved-source-history")
        with patch("fundlab.collectors.session.acquire_session", side_effect=AssertionError("no browser or network")):
            result = create_premium_chart(self.config, "000001")
        output = Path(result["chart"])
        html = output.read_text(encoding="utf-8")
        inventory = ScriptInventory()
        inventory.feed(html)
        self.assertEqual(inventory.external_scripts, [])
        self.assertIn("Plotly.newPlot", html)
        self.assertIn("近1个月", html)
        self.assertEqual(result["valid_samples"], 1)
        self.assertTrue(output.is_relative_to(self.config.data_dir / "analysis"))
        self.assertEqual(history.read_bytes(), b"saved-source-history")

    def test_publish_failure_preserves_previous_chart(self):
        result = create_premium_chart(self.config, "000001")
        output = Path(result["chart"])
        output.write_text("previous-chart", encoding="utf-8")
        with patch("fundlab.services.analysis.os.replace", side_effect=PermissionError("locked")):
            with self.assertRaises(FundlabError) as caught:
                create_premium_chart(self.config, "000001")
        self.assertEqual(caught.exception.code, "CHART_WRITE_FAILED")
        self.assertEqual(output.read_text(encoding="utf-8"), "previous-chart")
        self.assertFalse(list(output.parent.glob(".chart-*.tmp")))

    def test_untrusted_fund_name_is_plain_text(self):
        series = premium_series([{"price_date": "2020-01-01", "source_premium": "0"}])
        html = render_premium_timeline(replace(self.fund, name="</script><img src=x onerror=alert(1)>"), series)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;img", html)

    def test_analysis_service_and_cli_use_local_snapshot(self):
        before = self.config.database_path.read_bytes()
        output = io.StringIO()
        with patch("fundlab.cli.AppConfig.default", return_value=self.config), \
             patch("sys.argv", ["fundlab", "analyze", "--fund", "000001", "--ma", "1", "2"]), redirect_stdout(output):
            self.assertEqual(main(), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result, analyze_one(self.config, "000001", AnalysisRequest(ma_periods=(1, 2))))
        self.assertEqual(result["current"]["average_premiums"], {"1": "19.96", "2": None})
        self.assertEqual(result["window"]["missing_samples"], 1)
        self.assertEqual(self.config.database_path.read_bytes(), before)

    def test_invalid_period_does_not_write_analysis(self):
        output = io.StringIO()
        with patch("fundlab.cli.AppConfig.default", return_value=self.config), \
             patch("sys.argv", ["fundlab", "analyze", "--fund", "000001", "--ma", "0"]), redirect_stderr(output):
            self.assertEqual(main(), 1)
        self.assertIn("VALIDATION_ERROR", output.getvalue())
        self.assertFalse((self.config.data_dir / "analysis").exists())

    def test_cli_custom_premium_uses_percentage_points(self):
        output = io.StringIO()
        with patch("fundlab.cli.AppConfig.default", return_value=self.config), \
             patch("sys.argv", ["fundlab", "analyze", "--fund", "000001", "--premium", "12.5"]), redirect_stdout(output):
            self.assertEqual(main(), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["comparison"]["premium"], "12.5")
        self.assertEqual(result["comparison"]["percentile_rank"], "0")
        self.assertEqual(result["current"]["percentile_rank"], "100")


if __name__ == "__main__":
    unittest.main()
