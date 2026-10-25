"""调度时间、持久认领和中断恢复；仅测试替身可返回fixture，不接真实源站。"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

from fundlab.cli import main
from fundlab.collectors.session import ProfileLock
from fundlab.config import AppConfig
from fundlab.errors import FundlabError, SessionBusy
from fundlab.models import SourceWindow
from fundlab.parsers.history import parse_history
from fundlab.scheduling.rules import BEIJING, build_plan, count_slots, latest_slot, next_slot
from fundlab.scheduling.runner import run_due_updates, schedule_status
from fundlab.scheduling.store import ScheduleStore
from fundlab.services.update import update_lock, update_one
from fundlab.services.watchlist import WatchlistService
from fundlab.storage.repository import Repository
from fundlab.web.server import make_server
from test_data_storage import FUND, STAMP, fixture_row


def at(value):
    return datetime.fromisoformat(value).replace(tzinfo=BEIJING)


MONDAY = at("2026-10-19T18:00:00")
ENROLLED = at("2026-10-19T08:00:00").isoformat()


class ScheduleRuleTests(unittest.TestCase):
    def item(self, **values):
        return {"code": "000001", "name": "测试基金", "enabled": True,
                "added_at": ENROLLED, "updated_at": ENROLLED, **values}

    def test_exact_18_boundary_and_weekends(self):
        self.assertEqual(build_plan([self.item()], {}, MONDAY-timedelta(microseconds=1)), [])
        self.assertEqual(len(build_plan([self.item()], {}, MONDAY)), 1)
        friday = at("2026-10-23T18:00:00")
        sunday = at("2026-10-25T23:00:00")
        self.assertEqual(latest_slot(sunday), friday)
        self.assertEqual(next_slot(friday), at("2026-10-26T18:00:00"))
        self.assertEqual(next_slot(friday, inclusive=True), friday)

    def test_explicit_timezone_equivalence_and_naive_rejected(self):
        utc = MONDAY.astimezone(timezone.utc)
        other = MONDAY.astimezone(timezone(timedelta(hours=-7)))
        self.assertEqual(build_plan([self.item()], {}, utc), build_plan([self.item()], {}, other))
        with self.assertRaises(ValueError):
            next_slot(datetime(2026, 10, 19, 18))

    def test_exchange_holidays_filtered_and_unknown_year_blocked(self):
        day = at("2026-10-01T18:00:00")
        self.assertEqual(latest_slot(day), at("2026-09-30T18:00:00"))
        self.assertEqual(next_slot(day), at("2026-10-08T18:00:00"))
        with self.assertRaises(FundlabError):
            next_slot(at("2026-12-31T18:00:00"))

    def test_enrollment_after_due_and_reenable_skip_older_periods(self):
        item = self.item(added_at=(MONDAY+timedelta(seconds=1)).isoformat(), updated_at=(MONDAY+timedelta(seconds=1)).isoformat())
        self.assertEqual(build_plan([item], {}, MONDAY+timedelta(hours=1)), [])
        friday = at("2026-10-23T18:00:00")
        plan = build_plan([item], {}, friday)[0]
        self.assertEqual(plan["merged_count"], 4)
        item["updated_at"] = at("2026-10-23T12:00:00").isoformat()
        self.assertEqual(build_plan([item], {}, friday)[0]["merged_count"], 1)
        self.assertEqual(build_plan([self.item(enabled=False)], {}, friday), [])

    def test_merge_success_failed_and_clock_rollback(self):
        item = self.item()
        first = build_plan([item], {}, MONDAY)[0]
        friday = at("2026-10-23T18:00:00")
        self.assertEqual(build_plan([item], {}, friday)[0]["merged_count"], 5)
        first.update(status="succeeded")
        plan = build_plan([item], {item["code"]: first}, friday)[0]
        self.assertEqual(plan["merged_count"], 4)
        first.update(status="failed", attempts=3)
        plan = build_plan([item], {item["code"]: first}, friday)[0]
        self.assertEqual((plan["merged_count"], plan["attempts"]), (5, 0))
        self.assertEqual(build_plan([item], {item["code"]: plan}, MONDAY), [])

    def test_trading_count_skips_holiday_weekdays(self):
        self.assertEqual(count_slots(at("2026-09-24T18:00:00"),at("2026-10-09T18:00:00")), 6)


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        task_query = patch('fundlab.scheduling.windows_task.WindowsTask.query', return_value={'installed':False, 'sid':'test-user'})
        task_query.start(); self.addCleanup(task_query.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = AppConfig(Path(self.temp.name))
        self.watchlist = WatchlistService(self.config)
        self.store = ScheduleStore(self.config)

    def seed(self, code="000001"):
        repo = Repository(self.config.database_path)
        repo.start_run("seed-"+code, code, "initial")
        repo.commit_window(replace(FUND, code=code), parse_history([fixture_row(fund_id=code)], code, STAMP), "seed-"+code)
        with patch("fundlab.services.watchlist.utc_now", return_value=ENROLLED):
            self.watchlist.change("add", code)
        return repo

    def window(self, config, code):
        return SourceWindow(replace(FUND, code=code), [fixture_row(fund_id=code)], STAMP, {"kind": "test_only_window"})

    def job(self, code="000001"):
        return self.store.read()["latest"][code]

    def run_tick(self, now=MONDAY, **kwargs):
        return run_due_updates(self.config, now=now, **kwargs)

    def test_status_is_readonly_and_empty_run_records_skip_without_collection(self):
        self.assertEqual(schedule_status(self.config, now=MONDAY)["pending"], [])
        self.assertFalse(self.config.data_dir.exists())
        with patch("fundlab.scheduling.runner.update_one") as worker:
            result = self.run_tick()
        worker.assert_not_called()
        self.assertEqual(result["status"], "skipped_empty")
        self.assertEqual(self.store.read()["check"]["status"], "skipped_empty")
        self.assertFalse(self.config.database_path.exists())

    def test_not_due_and_success_duplicate_after_reopen_do_not_collect(self):
        repo = self.seed()
        with patch("fundlab.services.update.fetch_window", side_effect=self.window) as fetch:
            self.assertEqual(self.run_tick(MONDAY-timedelta(seconds=1))["status"], "not_due")
            first = self.run_tick()
            self.assertEqual(first["status"], "succeeded")
            self.assertEqual(self.run_tick()["status"], "not_due")
            fetch.assert_called_once()
        self.assertEqual(ScheduleStore(self.config).read()["latest"]["000001"]["status"], "succeeded")
        automatic = [r for r in repo.list_runs() if r["trigger"].startswith("scheduled:")]
        self.assertEqual(len(automatic), 1)
        self.assertEqual(automatic[0]["trigger"], "scheduled:"+MONDAY.isoformat())

    def test_missed_days_merged_once_and_next_week_runs_once_more(self):
        self.seed()
        friday = at("2026-10-23T18:00:00")
        with patch("fundlab.services.update.fetch_window", side_effect=self.window) as fetch:
            self.assertEqual(self.run_tick(friday+timedelta(days=1))["status"], "skipped_closed")
            self.assertEqual(self.run_tick(friday+timedelta(days=2))["status"], "skipped_closed")
            fetch.assert_not_called()
            self.run_tick(friday)
            self.assertEqual(self.job()["merged_count"], 5)
            self.run_tick(at("2026-10-26T18:00:00"))
            self.assertEqual(fetch.call_count, 2)
        self.assertEqual(self.job()["merged_count"], 1)

    def test_automatic_lock_prevents_second_runner(self):
        self.seed()
        entered, release = Event(), Event()
        def blocked(config, code):
            entered.set()
            release.wait(5)
            return self.window(config, code)
        with patch("fundlab.services.update.fetch_window", side_effect=blocked) as fetch, ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.run_tick)
            try:
                self.assertTrue(entered.wait(3))
                self.assertEqual(self.run_tick()["status"], "deferred")
                with self.assertRaises(FundlabError) as manual:
                    update_one(self.config, "000001")
                self.assertEqual(manual.exception.code, "UPDATE_BUSY")
            finally:
                release.set()
            self.assertEqual(first.result()["status"], "succeeded")
            fetch.assert_called_once()

    def test_manual_lock_defers_without_consuming_attempt_and_later_runs(self):
        self.seed()
        with patch("fundlab.services.update.fetch_window", side_effect=self.window) as fetch:
            with update_lock(self.config):
                self.assertEqual(self.run_tick()["status"], "pending")
            self.assertEqual(self.job()["attempts"], 0)
            self.assertEqual(self.job()["error_code"], "UPDATE_BUSY")
            self.run_tick(MONDAY+timedelta(minutes=1))
            fetch.assert_not_called()
            self.run_tick(MONDAY+timedelta(minutes=5))
            fetch.assert_called_once()

    def test_profile_busy_defers_and_does_not_consume_retry_budget(self):
        self.seed()
        with patch("fundlab.services.update.fetch_window", side_effect=SessionBusy()):
            self.run_tick()
        self.assertEqual((self.job()["status"], self.job()["attempts"]), ("planned", 0))
        with patch("fundlab.services.update.fetch_window", side_effect=self.window):
            self.assertEqual(self.run_tick(MONDAY+timedelta(minutes=5))["status"], "succeeded")

    def test_network_failure_backoff_three_attempt_cap_and_new_slot(self):
        self.seed()
        with patch("fundlab.services.update.fetch_window", side_effect=FundlabError("NETWORK_ERROR", "测试网络失败")) as fetch:
            for minute in (0, 1, 5, 10, 15):
                self.run_tick(MONDAY+timedelta(minutes=minute))
            self.run_tick(MONDAY+timedelta(minutes=20), retry_failed=True)
            self.assertEqual(fetch.call_count, 3)
            self.assertEqual(self.job()["attempts"], 3)
            self.run_tick(MONDAY+timedelta(days=1))
            self.assertEqual(fetch.call_count, 4)
        self.assertEqual((self.job()["attempts"], self.job()["merged_count"]), (1, 2))

    def test_auth_stops_remaining_browser_opens_and_explicit_retry_after_login(self):
        self.seed()
        self.seed("000002")
        with patch("fundlab.services.update.fetch_window", side_effect=FundlabError("AUTH_REQUIRED", "测试登录失效")) as fetch:
            self.run_tick()
            self.run_tick(MONDAY+timedelta(minutes=10))
            fetch.assert_called_once()
        self.assertEqual(self.job("000002")["attempts"], 0)
        self.assertEqual(self.job("000002")["error_code"], "AUTH_REQUIRED")
        with patch("fundlab.services.update.fetch_window", side_effect=self.window) as fetch:
            self.assertEqual(self.run_tick(MONDAY+timedelta(minutes=11), retry_failed=True)["status"], "succeeded")
            self.assertEqual(fetch.call_count, 2)

    def test_one_category_failure_does_not_block_other_fund(self):
        self.seed()
        self.seed("000002")
        def source(config, code):
            if code == "000001":
                raise FundlabError("UNSUPPORTED_CATEGORY", "测试不支持类别")
            return self.window(config, code)
        with patch("fundlab.services.update.fetch_window", side_effect=source) as fetch:
            self.run_tick()
            self.run_tick(MONDAY+timedelta(minutes=10))
            self.assertEqual(fetch.call_count, 2)
        self.assertEqual(self.job("000002")["status"], "succeeded")

    def test_export_failure_only_reexports_and_does_not_fetch_again(self):
        repo = self.seed()
        with patch("fundlab.services.update.fetch_window", side_effect=self.window) as fetch:
            with patch("fundlab.services.update.export_history", side_effect=FundlabError("EXPORT_ERROR", "测试文件占用")):
                self.run_tick()
            self.assertEqual((self.job()["phase"], self.job()["export_attempts"]), ("export", 1))
            self.assertEqual(self.run_tick(MONDAY+timedelta(minutes=5))["status"], "succeeded")
            fetch.assert_called_once()
        self.assertEqual(self.job()["export_attempts"], 2)
        self.assertEqual(len([r for r in repo.list_runs() if r["mode"] == "export"]), 1)

    def test_export_retries_are_bounded_without_recollecting(self):
        self.seed()
        with patch("fundlab.services.update.fetch_window", side_effect=self.window) as fetch, patch(
                "fundlab.services.update.export_history", side_effect=FundlabError("EXPORT_ERROR", "测试失败")) as export:
            for minute in (0, 5, 10, 15):
                self.run_tick(MONDAY+timedelta(minutes=minute))
            self.assertEqual((fetch.call_count, export.call_count), (1, 3))
        self.assertEqual(self.job()["phase"], "export")

    def test_unstarted_disabled_or_removed_fund_is_skipped(self):
        self.seed()
        self.seed("000002")
        def source(config, code):
            self.watchlist.change("remove", "000002")
            return self.window(config, code)
        with patch("fundlab.services.update.fetch_window", side_effect=source) as fetch:
            self.run_tick()
            fetch.assert_called_once()
        self.assertEqual(self.job("000002")["status"], "skipped")

    def test_ledger_write_failure_before_worker_prevents_source(self):
        self.seed()
        with patch("fundlab.scheduling.store.ScheduleStore.save", side_effect=FundlabError("SCHEDULE_WRITE_FAILED", "测试写入失败")), patch(
                "fundlab.services.update.fetch_window") as fetch:
            with self.assertRaises(FundlabError):
                self.run_tick()
            fetch.assert_not_called()
        self.assertEqual(self.job()["status"], "planned")

    def test_success_with_failed_final_ledger_write_recovers_without_refetch(self):
        self.seed()
        original = ScheduleStore.save
        def fail_success(store, job):
            if job["status"] == "succeeded":
                raise FundlabError("SCHEDULE_WRITE_FAILED", "测试结果日志失败")
            return original(store, job)
        with patch("fundlab.services.update.fetch_window", side_effect=self.window) as fetch:
            with patch.object(ScheduleStore, "save", fail_success), self.assertRaises(FundlabError):
                self.run_tick()
            self.assertEqual(self.job()["status"], "running")
            self.run_tick(MONDAY+timedelta(minutes=5))
            fetch.assert_called_once()
        self.assertEqual(self.job()["status"], "succeeded")

    def test_crash_after_market_commit_recovers_as_export_without_fetch(self):
        self.seed()
        with patch("fundlab.services.update.fetch_window", side_effect=self.window) as fetch:
            with patch.object(Repository, "finish_run", side_effect=OSError("测试保存结果失败")), self.assertRaises(FundlabError):
                self.run_tick()
            self.assertEqual(self.job()["status"], "running")
            self.run_tick(MONDAY+timedelta(minutes=1))
            self.assertEqual(self.job()["phase"], "export")
            self.run_tick(MONDAY+timedelta(minutes=6))
            fetch.assert_called_once()
        self.assertEqual(self.job()["status"], "succeeded")

    def test_crash_before_market_run_releases_claim_for_safe_retry(self):
        self.seed()
        with patch("fundlab.scheduling.runner.update_one", side_effect=RuntimeError("测试启动前中断")), self.assertRaises(FundlabError):
            self.run_tick()
        self.assertEqual(self.job()["status"], "running")
        with patch("fundlab.services.update.fetch_window", side_effect=self.window) as fetch:
            self.run_tick(MONDAY+timedelta(minutes=1))
            fetch.assert_called_once()
        self.assertEqual(self.job()["attempts"], 2)

    def test_interrupt_after_commit_only_reexports_on_recovery(self):
        self.seed()
        with patch("fundlab.services.update.fetch_window", side_effect=self.window) as fetch:
            with patch("fundlab.services.update.export_history", side_effect=KeyboardInterrupt()), self.assertRaises(KeyboardInterrupt):
                self.run_tick()
            self.run_tick(MONDAY+timedelta(minutes=1))
            self.assertEqual(self.job()["phase"], "export")
            self.run_tick(MONDAY+timedelta(minutes=6))
            fetch.assert_called_once()
        self.assertEqual(self.job()["status"], "succeeded")

    def test_repeated_startup_interruptions_still_have_three_attempt_limit(self):
        self.seed()
        with patch("fundlab.scheduling.runner.update_one", side_effect=RuntimeError("测试启动前中断")) as worker:
            for minute in (0, 1, 2):
                with self.assertRaises(FundlabError):
                    self.run_tick(MONDAY+timedelta(minutes=minute))
            self.run_tick(MONDAY+timedelta(minutes=3), retry_failed=True)
            self.assertEqual(worker.call_count, 3)
        self.assertEqual(self.job()["attempts"], 3)

    def test_missing_market_database_blocks_unknown_result_recovery(self):
        self.seed()
        with patch("fundlab.scheduling.runner.update_one", side_effect=RuntimeError("测试中断")), self.assertRaises(FundlabError):
            self.run_tick()
        self.config.database_path.rename(self.config.database_path.with_suffix(".saved"))
        with patch("fundlab.scheduling.runner.update_one") as worker, self.assertRaises(FundlabError) as caught:
            self.run_tick(MONDAY+timedelta(minutes=1))
        self.assertEqual(caught.exception.code, "NO_DATA")
        worker.assert_not_called()
        self.assertEqual(self.job()["status"], "running")

    def test_corrupt_or_future_schedule_file_is_preserved(self):
        self.seed()
        with closing(sqlite3.connect(self.config.schedule_path)) as db:
            db.execute("PRAGMA user_version=99")
        before = self.config.schedule_path.read_bytes()
        with patch("fundlab.services.update.fetch_window") as fetch:
            for action in (lambda: schedule_status(self.config, now=MONDAY), self.run_tick):
                with self.assertRaises(FundlabError):
                    action()
            fetch.assert_not_called()
        self.assertEqual(before, self.config.schedule_path.read_bytes())

    def test_status_does_not_mutate_market_list_or_schedule_files(self):
        self.seed()
        with patch("fundlab.services.update.fetch_window", side_effect=self.window):
            self.run_tick()
        files = [self.config.database_path, self.config.watchlist_path, self.config.schedule_path]
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
        status = schedule_status(self.config, now=MONDAY+timedelta(days=1))
        self.assertEqual(status["pending_count"], 1)
        self.assertFalse(status["scheduler_enabled"])
        for path in files:
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before[path])

    def test_http_plan_is_readonly_rejects_parameters_and_has_no_run_endpoint(self):
        self.seed()
        server = make_server(self.config, 0)
        Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        opener = build_opener(ProxyHandler({}))
        base = f"http://127.0.0.1:{server.server_port}"
        with opener.open(base+"/api/schedule") as response:
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertFalse(json.load(response)["scheduler_enabled"])
        for path, code in (("/api/schedule?now=2000", 400), ("/api/schedule/run-due", 404)):
            with self.assertRaises(HTTPError) as caught:
                opener.open(base+path)
            self.assertEqual(caught.exception.code, code)
        self.assertFalse(self.config.schedule_path.exists())

    def test_cli_status_run_due_exit_codes_and_no_install_command(self):
        self.seed()
        with patch("fundlab.cli.AppConfig.default", return_value=self.config), patch("builtins.print"):
            with patch("sys.argv", ["fundlab", "schedule", "status"]):
                self.assertEqual(main(), 0)
            with patch("sys.argv", ["fundlab", "schedule", "run-due"]), patch(
                    "fundlab.scheduling.runner.run_due_updates", return_value={"status":"pending"}) as run:
                self.assertEqual(main(), 2)
                self.assertFalse(run.call_args.kwargs["retry_failed"])
            with patch("sys.argv", ["fundlab", "schedule", "run-due", "--retry-failed"]), patch(
                    "fundlab.scheduling.runner.run_due_updates", return_value={"status":"succeeded"}) as run:
                self.assertEqual(main(), 0)
                self.assertTrue(run.call_args.kwargs["retry_failed"])
        self.assertFalse(self.config.schedule_path.exists())


if __name__ == "__main__":
    unittest.main()
