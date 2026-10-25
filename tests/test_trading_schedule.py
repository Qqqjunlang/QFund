"""交易日和用户时间设置；网络行情替身及合成日历只用于临时测试。"""
from contextlib import closing
from dataclasses import replace
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from fundlab.cli import main
from fundlab.collectors.session import ProfileLock
from fundlab.config import AppConfig
from fundlab.errors import FundlabError
from fundlab.models import SourceWindow
from fundlab.parsers.history import parse_history
from fundlab.scheduling.calendar import TradingCalendar, load_calendar
from fundlab.scheduling.rules import build_plan, count_slots, latest_slot, next_slot
from fundlab.scheduling.runner import run_due_updates, schedule_status
from fundlab.scheduling.settings import ScheduleSettings
from fundlab.scheduling.store import ScheduleStore
from fundlab.services.watchlist import WatchlistService
from fundlab.storage.repository import Repository
from fundlab.web.server import make_server
from test_data_storage import FUND, STAMP, fixture_row
from test_scheduling import at, MONDAY, ENROLLED


class TradingScheduleTests(unittest.TestCase):
    def setUp(self):
        task_query = patch('fundlab.scheduling.windows_task.WindowsTask.query', return_value={'installed':False, 'sid':'test-user'})
        task_query.start(); self.addCleanup(task_query.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = AppConfig(Path(self.temp.name))
        self.settings = ScheduleSettings(self.config)
        self.store = ScheduleStore(self.config)

    def seed(self, enrolled=ENROLLED):
        repo = Repository(self.config.database_path)
        repo.start_run("test-seed", FUND.code, "initial")
        repo.commit_window(FUND, parse_history([fixture_row()], FUND.code, STAMP), "test-seed")
        with patch("fundlab.services.watchlist.utc_now", return_value=enrolled):
            WatchlistService(self.config).change("add", FUND.code)

    def set_time(self, value, when=ENROLLED):
        with patch("fundlab.scheduling.settings.utc_now", return_value=when):
            return self.settings.save(value)

    def window(self, config, code):
        return SourceWindow(FUND, [fixture_row()], STAMP, {"kind": "test_only_window"})

    def tick(self, now):
        return run_due_updates(self.config, now=now)

    def job(self):
        return self.store.read()["latest"][FUND.code]

    def test_verified_calendar_holidays_weekends_and_a_share_scope(self):
        calendar = load_calendar()
        for day in ("2026-01-01", "2026-02-23", "2026-02-14", "2026-05-04", "2026-05-09", "2026-10-01", "2026-10-10"):
            with self.subTest(day=day):
                self.assertFalse(calendar.is_open(date.fromisoformat(day)))
        self.assertTrue(calendar.is_open(date(2026,10,19)))
        self.assertEqual(count_slots(at("2026-01-01T18:00:00"),at("2026-12-31T18:00:00")),242)
        for day in (date(2025,12,31),date(2027,1,4)):
            with self.assertRaises(FundlabError):
                calendar.is_open(day)

    def test_corrupt_calendar_never_falls_back_to_weekdays(self):
        load_calendar.cache_clear()
        try:
            with patch("fundlab.scheduling.calendar.Path.read_text", return_value='{}'), self.assertRaises(FundlabError) as caught:
                load_calendar()
            self.assertEqual(caught.exception.code,"CALENDAR_INVALID")
        finally:
            load_calendar.cache_clear()

    def test_custom_clock_boundary_and_synthetic_leap_calendar(self):
        self.assertEqual(next_slot(at("2026-10-22T13:44:00"),update_time="13:45"),at("2026-10-22T13:45:00"))
        self.assertEqual(latest_slot(at("2026-10-22T13:45:00"),update_time="13:45"),at("2026-10-22T13:45:00"))
        synthetic = TradingCalendar(date(2028,1,1),date(2028,12,31),frozenset(),"2028-01-01",("test-only",))
        self.assertEqual(next_slot(at("2028-02-28T23:59:00"),update_time="00:00",calendar=synthetic),at("2028-02-29T00:00:00"))

    def test_default_read_does_not_create_any_files(self):
        self.assertEqual(self.settings.read()["update_time"],"18:00")
        self.assertEqual(list(self.config.root.iterdir()),[])

    def test_invalid_clock_does_not_create_files(self):
        for value in (None,True,1800,"8:30","24:00","12:60","18:00:00"," 18:00","１８:００",""):
            with self.subTest(value=value), self.assertRaises(FundlabError):
                self.settings.save(value)
        self.assertEqual(list(self.config.root.iterdir()),[])

    def test_saved_clock_survives_reopen_and_same_value_keeps_cutoff(self):
        first=self.set_time("20:30")
        self.assertEqual(ScheduleSettings(self.config).read(),first)
        self.assertEqual(self.set_time("20:30",(MONDAY+timedelta(days=1)).isoformat()),first)
        self.assertEqual(first["effective_from"],ENROLLED)

    def test_corrupt_settings_preserved_and_blocks_execution(self):
        self.seed()
        self.settings.path.write_bytes(b'{broken')
        with patch("fundlab.scheduling.runner.update_one") as worker, self.assertRaises(FundlabError):
            self.tick(MONDAY)
        worker.assert_not_called()
        self.assertEqual(self.settings.path.read_bytes(),b'{broken')

    def test_write_failure_keeps_old_time_and_cleans_own_temporary_file(self):
        self.set_time("20:30")
        before=self.settings.path.read_bytes()
        with patch("fundlab.scheduling.settings.os.replace",side_effect=OSError("test")),self.assertRaises(FundlabError):
            self.set_time("19:15")
        self.assertEqual(self.settings.path.read_bytes(),before)
        self.assertEqual(list(self.config.data_dir.glob('*.tmp')),[])

    def test_running_batch_prevents_time_change(self):
        with ProfileLock(self.config.local_dir/'scheduler.lock'),self.assertRaises(FundlabError) as caught:
            self.set_time("20:00")
        self.assertEqual(caught.exception.code,"SCHEDULE_BUSY")
        self.assertFalse(self.settings.path.exists())

    def test_saving_clock_does_not_start_collection_or_create_run_log(self):
        self.seed()
        before=self.config.database_path.read_bytes()
        with patch("fundlab.scheduling.runner.update_one") as worker:
            self.set_time("23:59")
            plan=schedule_status(self.config,now=MONDAY)
        worker.assert_not_called()
        self.assertEqual(plan["update_time"],"23:59")
        self.assertFalse(self.config.schedule_path.exists())
        self.assertEqual(self.config.database_path.read_bytes(),before)

    def test_custom_time_waits_until_exact_minute_and_runs_once(self):
        self.seed()
        self.set_time("13:45")
        with patch("fundlab.services.update.fetch_window",side_effect=self.window) as fetch:
            self.assertEqual(self.tick(at("2026-10-19T13:44:59"))["status"],"not_due")
            fetch.assert_not_called()
            self.assertEqual(self.tick(at("2026-10-19T13:45:00"))["status"],"succeeded")
            self.tick(at("2026-10-19T13:46:00"))
            fetch.assert_called_once()

    def test_closed_day_never_catches_up_and_reopening_merges_trading_days(self):
        self.seed(at("2026-09-29T08:00:00").isoformat())
        with patch("fundlab.services.update.fetch_window",side_effect=self.window) as fetch:
            self.assertEqual(self.tick(at("2026-10-01T20:00:00"))["status"],"skipped_closed")
            self.assertEqual(self.tick(at("2026-10-04T20:00:00"))["status"],"skipped_closed")
            self.assertEqual(self.tick(at("2026-10-08T17:59:00"))["status"],"not_due")
            fetch.assert_not_called()
            self.tick(at("2026-10-08T18:00:00"))
            fetch.assert_called_once()
        self.assertEqual(self.job()["merged_count"],3)

    def test_moving_later_does_not_run_same_successful_date_again(self):
        self.seed()
        with patch("fundlab.services.update.fetch_window",side_effect=self.window) as fetch:
            self.tick(MONDAY)
            self.set_time("20:00",at("2026-10-19T19:00:00").isoformat())
            self.tick(at("2026-10-19T20:00:00"))
            fetch.assert_called_once()
            self.tick(at("2026-10-20T20:00:00"))
            self.assertEqual(fetch.call_count,2)

    def test_moving_earlier_after_time_applies_to_next_trading_date(self):
        self.seed()
        self.set_time("15:00",at("2026-10-19T19:00:00").isoformat())
        with patch("fundlab.services.update.fetch_window",side_effect=self.window) as fetch:
            self.tick(at("2026-10-19T19:01:00"))
            fetch.assert_not_called()
            self.tick(at("2026-10-20T15:00:00"))
            fetch.assert_called_once()

    def test_changing_time_preserves_same_date_failed_attempt_budget(self):
        self.seed()
        with patch("fundlab.services.update.fetch_window",side_effect=FundlabError("NETWORK_ERROR","test")) as fetch:
            for minutes in (0,5,10):
                self.tick(MONDAY+timedelta(minutes=minutes))
            self.set_time("20:00",at("2026-10-19T19:00:00").isoformat())
            self.tick(at("2026-10-19T20:00:00"))
            self.assertEqual(fetch.call_count,3)
        self.assertEqual(self.job()["attempts"],3)

    def test_changing_time_preserves_pending_export_without_refetch(self):
        self.seed()
        with patch("fundlab.services.update.fetch_window",side_effect=self.window) as fetch:
            with patch("fundlab.services.update.export_history",side_effect=FundlabError("EXPORT_ERROR","test")):
                self.tick(MONDAY)
            self.set_time("20:00",at("2026-10-19T19:00:00").isoformat())
            self.tick(at("2026-10-19T20:00:00"))
            fetch.assert_called_once()
        self.assertEqual(self.job()["status"],"succeeded")
        self.assertEqual(self.job()["phase"],"export")

    def test_skipped_fund_can_rejoin_later_time_same_day_without_repeating_success(self):
        self.seed()
        repo=Repository(self.config.database_path)
        second=replace(FUND,code='000002')
        repo.start_run('seed-second',second.code,'initial')
        repo.commit_window(second,parse_history([fixture_row(fund_id=second.code)],second.code,STAMP),'seed-second')
        with patch('fundlab.services.watchlist.utc_now',return_value=ENROLLED):
            WatchlistService(self.config).change('add',second.code)
        def first_source(config,code):
            WatchlistService(config).change('remove',second.code)
            return self.window(config,code)
        with patch('fundlab.services.update.fetch_window',side_effect=first_source):
            self.tick(MONDAY)
        self.assertEqual(self.store.read()['latest'][second.code]['status'],'skipped')
        self.set_time('20:00',at('2026-10-19T19:00:00').isoformat())
        with patch('fundlab.services.watchlist.utc_now',return_value=at('2026-10-19T19:05:00').isoformat()):
            WatchlistService(self.config).change('add',second.code)
        window=SourceWindow(second,[fixture_row(fund_id=second.code)],STAMP,{'kind':'test_only_window'})
        with patch('fundlab.services.update.fetch_window',return_value=window) as fetch:
            self.assertEqual(self.tick(at('2026-10-19T20:00:00'))['status'],'succeeded')
            fetch.assert_called_once_with(self.config,second.code)

    def test_unknown_year_is_visible_and_runner_stops_before_worker(self):
        self.seed()
        unknown=at("2027-01-04T20:00:00")
        plan=schedule_status(self.config,now=unknown)
        self.assertFalse(plan["calendar_ready"])
        self.assertIsNotNone(plan["calendar_error"])
        self.assertIsNone(plan["next_slot"])
        with patch("fundlab.scheduling.runner.update_one") as worker,self.assertRaises(FundlabError):
            self.tick(unknown)
        worker.assert_not_called()
        self.assertFalse(self.config.schedule_path.exists())

    def test_year_end_can_finish_known_day_and_warn_about_next_year(self):
        self.seed()
        now=at("2026-12-31T20:00:00")
        plan=schedule_status(self.config,now=now)
        self.assertTrue(plan["calendar_ready"])
        self.assertIsNone(plan["next_slot"])
        self.assertIsNotNone(plan["calendar_warning"])
        with patch("fundlab.services.update.fetch_window",side_effect=self.window) as fetch:
            self.tick(now)
            fetch.assert_called_once()

    def start_server(self):
        server=make_server(self.config,0)
        Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.opener=build_opener(ProxyHandler({}))
        self.base=f'http://127.0.0.1:{server.server_port}'
        with self.opener.open(self.base+'/api/session') as response:
            self.token=json.load(response)['csrf_token']

    def post(self,body,headers=None):
        return self.opener.open(Request(self.base+'/api/schedule/settings',json.dumps(body).encode(),
            {'Content-Type':'application/json','X-Fundlab-CSRF':self.token,**(headers or {})}))

    def test_http_saves_custom_time_without_market_write_or_collection(self):
        self.seed()
        self.start_server()
        before=self.config.database_path.read_bytes()
        with self.post({'update_time':'20:30'}) as response:
            result=json.load(response)
        self.assertEqual(result['update_time'],'20:30')
        self.assertFalse(result['scheduler_enabled'])
        self.assertEqual(self.config.database_path.read_bytes(),before)
        self.assertFalse(self.config.schedule_path.exists())

    def test_http_rejects_forged_requests_bad_times_and_extra_fields(self):
        self.start_server()
        cases=[({'update_time':'20:30'},{'Origin':'https://example.invalid'},403),
               ({'update_time':'20:30'},{'X-Fundlab-CSRF':'wrong'},403),
               ({'update_time':'24:00'},None,400),({'update_time':True},None,400),
               ({'update_time':'20:30','enabled':True},None,400)]
        for body,headers,status in cases:
            with self.subTest(body=body),self.assertRaises(HTTPError) as caught:
                self.post(body,headers)
            self.assertEqual(caught.exception.code,status)
        self.assertFalse(self.settings.path.exists())

    def test_cli_set_time_uses_same_store_without_running_collection(self):
        with patch('fundlab.cli.AppConfig.default',return_value=self.config),patch('sys.argv',['fundlab','schedule','set-time','20:30']),patch('builtins.print'):
            self.assertEqual(main(),0)
        self.assertEqual(self.settings.read()['update_time'],'20:30')
        self.assertFalse(self.config.schedule_path.exists())


if __name__=='__main__':
    unittest.main()
