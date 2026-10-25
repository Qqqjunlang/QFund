"""名单持久化与HTTP边界；全部写临时目录，不采集真实行情。"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from fundlab.config import AppConfig
from fundlab.errors import FundlabError
from fundlab.parsers.history import parse_history
from fundlab.services.watchlist import WatchlistService
from fundlab.storage.repository import Repository
from fundlab.web.server import make_server
from test_data_storage import FUND, STAMP, fixture_row


class WatchlistTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = AppConfig(Path(self.temp.name))
        self.service = WatchlistService(self.config)

    def seed(self, code=FUND.code):
        repo = Repository(self.config.database_path)
        repo.start_run("seed-" + code, code, "initial")
        repo.commit_window(replace(FUND, code=code), parse_history([fixture_row(fund_id=code)], code, STAMP), "seed-" + code)
        return repo

    def test_read_empty_does_not_create_database_or_directories(self):
        state = self.service.state()
        self.assertEqual(state["items"], [])
        self.assertEqual(state["available_funds"], [])
        self.assertNotIn("scheduler_enabled", state)  # 系统任务状态由/api/schedule提供。
        self.assertEqual(list(self.config.root.iterdir()), [])

    def test_membership_survives_new_service_and_duplicate_add_keeps_disabled(self):
        self.seed()
        first = self.service.change("add", FUND.code)["items"][0]
        self.assertEqual(first["code"], "000001")
        self.assertTrue(first["enabled"])
        self.service.change("set_enabled", FUND.code, False)
        before = self.service.state()["items"][0]
        self.service.change("add", FUND.code)
        self.assertEqual(WatchlistService(self.config).state()["items"], [before])
        self.assertIsNone(before["last_auto_result"])
        self.assertEqual(before["added_at"], first["added_at"])
        self.service.change("set_enabled", FUND.code, True)
        self.assertEqual(self.service.state()["enabled_count"], 1)

    def test_invalid_code_or_unknown_local_fund_creates_no_list(self):
        self.seed()
        for code in (None, 1, True, "1", "０００００１", "999999", "../foo"):
            with self.subTest(code=code), self.assertRaises(FundlabError):
                self.service.change("add", code)
        self.assertFalse(self.config.watchlist_path.exists())

    def test_enable_requires_boolean_and_existing_entry(self):
        self.seed()
        self.service.change("add", FUND.code)
        before = self.service.state()
        for value in (1, 0, "false", None, [], {}):
            with self.subTest(value=value), self.assertRaises(FundlabError):
                self.service.change("set_enabled", FUND.code, value)
        self.assertEqual(before, self.service.state())
        with self.assertRaises(FundlabError):
            self.service.change("set_enabled", "999999", False)

    def test_remove_repeat_keeps_market_database_archive_and_runs(self):
        repo = self.seed()
        self.config.funds_dir.mkdir()
        archive = self.config.funds_dir / "keep.csv"
        archive.write_bytes(b"existing archive")
        before = self.config.database_path.read_bytes()
        runs = repo.list_runs()
        with patch("fundlab.services.update.update_one", side_effect=AssertionError("名单不应采集")):
            self.service.change("add", FUND.code)
            self.service.change("set_enabled", FUND.code, False)
            self.service.change("remove", FUND.code)
            self.service.change("remove", FUND.code)
        self.assertEqual(self.service.state()["items"], [])
        self.assertEqual(self.config.database_path.read_bytes(), before)
        self.assertEqual(repo.list_runs(), runs)
        self.assertEqual(archive.read_bytes(), b"existing archive")

    def test_missing_local_data_can_disable_or_remove_but_cannot_enable(self):
        self.seed()
        self.service.change("add", FUND.code)
        # 模拟迁移后缺少行情库；不移动真实项目文件。
        hidden = self.config.database_path.with_suffix(".saved")
        self.config.database_path.rename(hidden)
        state = self.service.state()
        self.assertFalse(state["items"][0]["available"])
        self.service.change("set_enabled", FUND.code, False)
        with self.assertRaises(FundlabError):
            self.service.change("set_enabled", FUND.code, True)
        self.service.change("remove", FUND.code)
        self.assertFalse(self.config.database_path.exists())

    def test_parallel_adds_keep_all_codes_and_single_row_per_code(self):
        self.seed()
        self.seed("000002")
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(WatchlistService(self.config).change, "add", code)
                       for code in ["000001", "000002"] * 6]
            for future in futures:
                future.result()
        self.assertEqual([item["code"] for item in self.service.state()["items"]], ["000001", "000002"])

    def test_future_version_or_corrupt_file_is_preserved(self):
        self.seed()
        with closing(sqlite3.connect(self.config.watchlist_path)) as db:
            db.execute("PRAGMA user_version=99")
        before = self.config.watchlist_path.read_bytes()
        for action in (lambda: self.service.state(), lambda: self.service.change("add", FUND.code)):
            with self.assertRaises(FundlabError):
                action()
            self.assertEqual(self.config.watchlist_path.read_bytes(), before)
        self.config.watchlist_path.write_bytes(b"invalid sqlite file")
        with self.assertRaises(FundlabError):
            self.service.change("remove", FUND.code)
        self.assertEqual(self.config.watchlist_path.read_bytes(), b"invalid sqlite file")

    def test_locked_list_reports_failure_and_keeps_membership(self):
        self.seed()
        self.service.change("add", FUND.code)
        with patch("fundlab.services.watchlist.sqlite3.connect", side_effect=sqlite3.OperationalError("locked")):
            with self.assertRaises(FundlabError) as caught:
                self.service.change("remove", FUND.code)
        self.assertEqual(caught.exception.code, "WATCHLIST_WRITE_FAILED")
        self.assertEqual(len(self.service.state()["items"]), 1)

    def start_server(self):
        server = make_server(self.config, 0)
        Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base = f"http://127.0.0.1:{server.server_port}"
        self.opener = build_opener(ProxyHandler({}))
        with self.opener.open(self.base + "/api/session") as response:
            self.token = json.load(response)["csrf_token"]

    def post(self, action, body, *, headers=None):
        request = Request(self.base + "/api/watchlist/" + action, json.dumps(body).encode(),
                          {"Content-Type": "application/json", "X-Fundlab-CSRF": self.token, **(headers or {})})
        return self.opener.open(request)

    def test_http_roundtrip_restart_and_no_task_started(self):
        self.seed()
        self.start_server()
        with self.post("add", {"code": FUND.code}) as response:
            self.assertEqual(json.load(response)["enabled_count"], 1)
        with self.post("set-enabled", {"code": FUND.code, "enabled": False}) as response:
            self.assertFalse(json.load(response)["items"][0]["enabled"])
        with self.opener.open(self.base + "/api/watchlist") as response:
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(json.load(response), WatchlistService(self.config).state())
        with self.opener.open(self.base + "/api/tasks") as response:
            self.assertIsNone(json.load(response)["latest"])
        with self.post("remove", {"code": FUND.code}) as response:
            self.assertEqual(json.load(response)["items"], [])

    def test_http_rejects_forged_origin_csrf_host_and_extra_fields(self):
        self.seed()
        self.start_server()
        for headers in ({"Origin": "https://example.invalid"}, {"X-Fundlab-CSRF": "wrong"}, {"Host": "example.invalid"}):
            with self.subTest(headers=headers), self.assertRaises(HTTPError) as caught:
                self.post("add", {"code": FUND.code}, headers=headers)
            self.assertEqual(caught.exception.code, 403)
        for body in ({"code": FUND.code, "name": "伪造"}, {"code": 1}, {}):
            with self.subTest(body=body), self.assertRaises(HTTPError) as caught:
                self.post("add", body)
            self.assertEqual(caught.exception.code, 400)
        for action, body in (("set-enabled", {"code": FUND.code, "enabled": "false"}),
                             ("remove?extra=1", {"code": FUND.code})):
            with self.assertRaises(HTTPError) as caught:
                self.post(action, body)
            self.assertEqual(caught.exception.code, 400)
        with self.assertRaises(HTTPError) as caught:
            self.opener.open(self.base + "/api/watchlist?code=000001")
        self.assertEqual(caught.exception.code, 400)
        self.assertFalse(self.config.watchlist_path.exists())


if __name__ == "__main__":
    unittest.main()
