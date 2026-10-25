"""本地真实录制的离线衔接检查：只替代浏览器边界，不联网、不碰真实数据库。

样本在忽略目录，不随代码发布；缺少本地样本时跳过，不能当作实时站点验证。
"""
from contextlib import closing, contextmanager
from copy import deepcopy
import csv
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from fundlab.config import AppConfig
from fundlab.parsers.history import FIELD_DEFINITIONS
from fundlab.probe import check_session
from fundlab.exports.service import fund_folder
from fundlab.services.update import update_one
from fundlab.storage.repository import Repository

ROOT = AppConfig.default().root
ARCHIVE = ROOT / ".local/evidence/history-513100.json"
DOM_ARCHIVE = ROOT / ".local/evidence/interactive-login.json"
DATABASE = ROOT / "data/fundlab.sqlite3"


@unittest.skipUnless(ARCHIVE.exists() and DOM_ARCHIVE.exists() and DATABASE.exists(),
                     "需要本机已保存的阶段一录制及阶段二真实数据库；不自动联网或生成假样本")
class StageHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="基金衔接 test ")
        self.addCleanup(self.temp.cleanup)
        self.config = AppConfig(Path(self.temp.name))
        self.old = json.loads(ARCHIVE.read_text(encoding="utf-8"))["rows"]
        self.dom = json.loads(DOM_ARCHIVE.read_text(encoding="utf-8"))
        # 只读连接，禁止以复查为由修改真实行情库。
        with closing(sqlite3.connect(DATABASE.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            self.current = [json.loads(row[0]) for row in db.execute(
                "SELECT raw_json FROM history h JOIN funds f ON f.id=h.fund_id WHERE f.code='513100' ORDER BY price_date DESC")]
        progress = patch("fundlab.services.update.print")
        progress.start()
        self.addCleanup(progress.stop)

    @contextmanager
    def browser_boundary(self, rows, *, auth_required=False):
        """复用真实响应和表格录制，只有浏览器对象是测试替身。"""
        page = MagicMock()
        page.url = "https://www.jisilu.cn/account/login/recorded" if auth_required else "https://www.jisilu.cn/data/qdii/detail/513100"
        page.goto.return_value.status = 200
        page.title.return_value = self.dom["title"]
        table = page.locator.return_value
        table.count.return_value = 1
        def locate(selector):
            element = MagicMock()
            if selector == "th":
                element.all_text_contents.return_value = self.dom["tables"][0]["headers"]
            elif selector == "tbody tr":
                element.evaluate_all.return_value = self.dom["tables"][0]["rows"]
            return element
        table.locator.side_effect = locate
        response = MagicMock()
        response.url = "https://www.jisilu.cn/data/qdii/detail_hists/"
        response.status = 200
        response.request.method = "POST"
        response.request.post_data = "is_search=1&fund_id=513100&rp=50&page=1"
        response.json.return_value = {"page": 1, "rows": deepcopy(rows)}
        pending = MagicMock()
        pending.value = response
        page.expect_response.return_value.__enter__.return_value = pending
        page.expect_response.return_value.__exit__.return_value = False
        context = MagicMock()
        context.new_page.return_value = page
        with patch("fundlab.collectors.jisilu_qdii.acquire_session") as acquire:
            acquire.return_value.__enter__.return_value = context
            acquire.return_value.__exit__.return_value = False
            yield page, acquire, response

    def test_recorded_stage1_data_flows_through_stage2_and_accumulates(self):
        before_count = len(self.old)
        with self.browser_boundary(self.old) as (page, acquire, response):
            self.assertEqual(check_session(page), "HISTORY_VISIBLE")
            first = update_one(self.config, "513100")
            self.assertEqual(first.status, "succeeded")
            self.assertEqual(first.inserted, before_count)
            acquire.assert_called_once_with(self.config, interactive=True)
            predicate = page.expect_response.call_args.args[0]
            self.assertTrue(predicate(response))
        with self.browser_boundary(self.current):
            second = update_one(self.config, "513100")
        with self.browser_boundary(self.current):
            third = update_one(self.config, "513100")
        expected_dates = {r["cell"]["price_dt"] for r in self.old + self.current}
        old_dates = {r["cell"]["price_dt"] for r in self.old}
        self.assertEqual(second.inserted, len(expected_dates - old_dates))
        self.assertEqual((third.status, third.inserted, third.updated), ("succeeded", 0, 0))
        self.assertEqual(second.total, len(expected_dates))
        self.assertEqual(third.total, len(expected_dates))
        repo = Repository(self.config.database_path)
        stored = repo.query_history("513100")
        self.assertEqual({r["price_date"] for r in stored}, expected_dates)
        self.assertFalse(third.coverage["full_history_verified"])
        fund = repo.get_fund("513100")
        folder = self.config.funds_dir / fund_folder(fund)
        with (folder / "history.csv").open(encoding="utf-8-sig", newline="") as handle:
            exported = list(csv.reader(handle))
        self.assertEqual(len(exported)-1, len(expected_dates))
        self.assertEqual(exported[0], [field[2] for field in FIELD_DEFINITIONS])
        self.assertEqual(len(repo.list_runs()), 3)

    def test_stage1_login_expiry_maps_to_stage2_failed_run_without_data_loss(self):
        with self.browser_boundary(self.old):
            first = update_one(self.config, "513100")
        self.assertEqual(first.status, "succeeded")
        with self.browser_boundary(self.old, auth_required=True) as (page, acquire, response):
            self.assertEqual(check_session(page), "AUTH_REQUIRED")
            failed = update_one(self.config, "513100")
        self.assertEqual((failed.status, failed.error_code), ("failed", "AUTH_REQUIRED"))
        repo = Repository(self.config.database_path)
        self.assertEqual(len(repo.query_history("513100")), len(self.old))
        self.assertEqual(repo.list_runs(1)[0]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
