"""网页采集编排与写入边界；人工输入/替身仅在本测试使用。"""
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
from threading import Event, Thread
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from fundlab.config import AppConfig
from fundlab.errors import FundlabError
from fundlab.models import SourceWindow, UpdateResult
from fundlab.services.login import login_session
from fundlab.services.search import search_candidates
from fundlab.storage.repository import read_history_snapshot
from fundlab.web.server import make_server
from fundlab.web.tasks import ACTIVE, TaskManager
from test_data_storage import FUND, STAMP, fixture_row


class WebCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = AppConfig(Path(self.temp.name))

    def finished(self, manager, job):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            value = manager.get(job['id'])
            if value['status'] not in ACTIVE and value['completed_at']:
                return value
            time.sleep(.01)
        self.fail('测试任务未结束')

    def test_same_request_only_runs_once_and_other_request_is_busy(self):
        manager = TaskManager(self.config)
        gate = Event()
        def search(*args):
            gate.wait(3)
            return {'matches': [], 'message': '测试空目录'}
        with patch('fundlab.web.tasks.search_candidates', side_effect=search) as worker:
            first = manager.submit('search', {'query': '测试'}, 'request-one-123456')
            duplicate = manager.submit('search', {'query': '测试'}, 'request-one-123456')
            self.assertEqual(first['id'], duplicate['id'])
            with self.assertRaises(FundlabError) as caught:
                manager.submit('search', {'query': '测试'}, 'request-two-123456')
            self.assertEqual(caught.exception.code, 'TASK_BUSY')
            gate.set()
            self.finished(manager, first)
            manager.submit('search', {'query': '测试'}, 'request-one-123456')
            self.assertEqual(worker.call_count, 1)

    def test_conflicting_id_and_invalid_inputs_do_not_start_jobs(self):
        manager = TaskManager(self.config)
        with patch('fundlab.web.tasks.search_candidates', return_value={'matches': [], 'message': '测试'}):
            job = manager.submit('search', {'query': '测试'}, 'request-one-123456')
            self.finished(manager, job)
        with self.assertRaises(FundlabError) as caught:
            manager.submit('update', {'code': '000001'}, 'request-one-123456')
        self.assertEqual(caught.exception.code, 'REQUEST_CONFLICT')
        for payload in ({'code': '123'}, {'code': None}):
            with self.assertRaises(FundlabError):
                manager.submit('update', payload, 'request-two-123456')
        self.assertEqual(len(manager.jobs), 1)

    def test_actual_update_pipeline_and_local_export_retry(self):
        manager = TaskManager(self.config)
        window = SourceWindow(FUND, [fixture_row()], STAMP, {'kind': 'source_window'})
        with patch('fundlab.services.update.fetch_window', return_value=window), patch(
                'fundlab.services.update.export_history', side_effect=FundlabError('EXPORT_ERROR', '测试导出失败')):
            job = self.finished(manager, manager.submit('update', {'code': FUND.code}, 'request-save-123456'))
        self.assertEqual(job['status'], 'export_failed')
        self.assertEqual(job['result']['inserted'], 1)
        self.assertEqual(len(read_history_snapshot(self.config.database_path, FUND.code).rows), 1)
        with patch('fundlab.services.update.fetch_window', side_effect=AssertionError('导出不得联网')):
            recovered = self.finished(manager, manager.submit('export', {'code': FUND.code}, 'request-export-123456'))
        self.assertEqual(recovered['status'], 'succeeded')
        self.assertNotIn('exports', recovered['result'])
        self.assertTrue((self.config.funds_dir / '000001_测试专用基金' / 'history.xlsx').is_file())

    def test_auth_failure_has_page_login_hint_and_unknown_error_is_sanitized(self):
        manager = TaskManager(self.config)
        with patch('fundlab.web.tasks.search_candidates', side_effect=FundlabError('AUTH_REQUIRED', 'CLI登录旧提示')):
            job = self.finished(manager, manager.submit('search', {'query': '测试'}, 'request-auth-123456'))
        self.assertEqual(job['error_code'], 'AUTH_REQUIRED')
        self.assertIn('专用 Edge 登录', job['message'])
        with patch('fundlab.web.tasks.search_candidates', side_effect=RuntimeError('不应公开的测试内部细节')):
            job = self.finished(manager, manager.submit('search', {'query': '测试'}, 'request-error-123456'))
        self.assertNotIn('内部细节', json.dumps(job, ensure_ascii=False))

    def test_restarted_pending_job_is_interrupted_not_replayed(self):
        manager = TaskManager(self.config)
        with patch('fundlab.web.tasks.search_candidates', return_value={'matches': [], 'message': '测试'}):
            job = self.finished(manager, manager.submit('search', {'query': '测试'}, 'request-restart-123456'))
        job.update(status='running', completed_at=None)
        manager.path.write_text(json.dumps([job]), encoding='utf-8')
        with patch('fundlab.web.tasks.search_candidates', side_effect=AssertionError('不能自动重放')):
            restored = TaskManager(self.config)
            value = restored.submit('search', {'query': '测试'}, 'request-restart-123456')
        self.assertEqual(value['status'], 'interrupted')
        self.assertIsNone(restored.state()['active'])

    def test_failed_task_record_write_prevents_operation(self):
        manager = TaskManager(self.config)
        with patch.object(manager, '_save', side_effect=OSError('测试磁盘写入失败')), patch('fundlab.web.tasks.update_one') as worker:
            with self.assertRaises(FundlabError):
                manager.submit('update', {'code': FUND.code}, 'request-disk-123456')
            worker.assert_not_called()
        self.assertIsNone(manager.state()['latest'])

    def test_cancel_login_and_reject_cancelling_update(self):
        manager = TaskManager(self.config)
        ready = Event()
        def login(*args, progress, stop_event):
            progress('waiting_login')
            ready.set()
            stop_event.wait(3)
            raise FundlabError('LOGIN_CANCELLED', '已取消')
        with patch('fundlab.web.tasks.login_session', side_effect=login):
            first = manager.submit('login', {}, 'request-login-123456')
            self.assertTrue(ready.wait(2))
            manager.cancel_login(first['id'])
            self.assertEqual(self.finished(manager, first)['status'], 'cancelled')
        with patch('fundlab.web.tasks.update_one', return_value=UpdateResult('test', FUND.code, status='failed')):
            job = self.finished(manager, manager.submit('update', {'code': FUND.code}, 'request-update-123456'))
        with self.assertRaises(FundlabError):
            manager.cancel_login(job['id'])

    def test_login_checks_table_without_accessing_cookies(self):
        page = SimpleNamespace(goto=lambda *a, **k: None, is_closed=lambda: False)
        context = SimpleNamespace(pages=[page])
        with patch('fundlab.services.login.acquire_session', return_value=nullcontext(context)) as acquire, patch(
                'fundlab.services.login.check_session', return_value='HISTORY_VISIBLE'):
            result = login_session(self.config)
        self.assertTrue(result['authenticated'])
        self.assertTrue(acquire.call_args.kwargs['interactive'])
        stop = Event()
        stop.set()
        with patch('fundlab.services.login.acquire_session') as acquire:
            with self.assertRaises(FundlabError) as caught:
                login_session(self.config, stop_event=stop)
            acquire.assert_not_called()
        self.assertEqual(caught.exception.code, 'LOGIN_CANCELLED')

    def test_exact_code_does_not_fetch_catalog_and_partial_name_keeps_candidates(self):
        with patch('fundlab.services.search.verify_catalog', side_effect=AssertionError('代码不用目录')):
            result = search_candidates(self.config, '000001')
        self.assertEqual(result['matches'][0]['code'], '000001')
        with patch('fundlab.services.search.verify_catalog', return_value={
                'matches': [{'code': '000001', 'name': '测试名称', 'category': 'QDII'}], 'missing_sources': ['/fixture']}) as lookup:
            result = search_candidates(self.config, '测试')
        self.assertTrue(result['partial'])
        self.assertEqual(len(result['matches']), 1)
        self.assertFalse(lookup.call_args.kwargs['persist_evidence'])

    def test_progress_callback_failure_does_not_break_update(self):
        from fundlab.services.update import update_one
        stages = []
        def progress(stage):
            stages.append(stage)
            raise RuntimeError('测试通知失败')
        window = SourceWindow(FUND, [fixture_row()], STAMP, {})
        with patch('fundlab.services.update.fetch_window', return_value=window):
            result = update_one(self.config, FUND.code, progress=progress)
        self.assertEqual(result.status, 'succeeded')
        self.assertEqual(stages, ['fetching', 'parsing', 'saving', 'exporting'])

    def start_server(self):
        server = make_server(self.config, 0)
        Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_port}'

    def token(self, base):
        with urlopen(base + '/api/session') as response:
            return json.load(response)['csrf_token']

    def post(self, base, route, data, token='', origin=None):
        headers = {'Content-Type': 'application/json', 'X-Fundlab-CSRF': token}
        if origin is not None:
            headers['Origin'] = origin
        return urlopen(Request(base + route, data=json.dumps(data).encode(), headers=headers))

    def test_http_post_needs_token_and_matching_origin(self):
        base = self.start_server()
        payload = {'query': '000001', 'request_id': 'request-http-123456'}
        for token, origin in (('', None), (self.token(base), 'https://example.invalid'), (self.token(base), 'null')):
            with self.assertRaises(HTTPError) as caught:
                self.post(base, '/api/tasks/search', payload, token, origin)
            self.assertEqual(caught.exception.code, 403)
        with urlopen(base + '/api/tasks') as response:
            self.assertIsNone(json.load(response)['latest'])

    def test_http_accepts_job_and_duplicate_id_reuses_it(self):
        base = self.start_server()
        token = self.token(base)
        payload = {'query': '000001', 'request_id': 'request-http-123456'}
        with self.post(base, '/api/tasks/search', payload, token, base) as response:
            first = json.load(response)
            self.assertEqual(response.status, 202)
        with self.post(base, '/api/tasks/search', payload, token, base) as response:
            duplicate = json.load(response)
        self.assertEqual(first['id'], duplicate['id'])
        with urlopen(base + '/api/task?id='+first['id']) as response:
            self.assertEqual(json.load(response)['kind'], 'search')

    def test_http_bad_inputs_get_and_extra_fields_cannot_start_collection(self):
        base = self.start_server()
        token = self.token(base)
        for payload in ({'code': None, 'request_id': 'request-http-123456'}, {'code': '123', 'request_id': 'request-http-123456'},
                        {'code': '000001', 'mode': 'full', 'request_id': 'request-http-123456'}, []):
            with self.assertRaises(HTTPError) as caught:
                self.post(base, '/api/tasks/update', payload, token, base)
            self.assertEqual(caught.exception.code, 400)
        with self.assertRaises(HTTPError) as caught:
            urlopen(base + '/api/tasks/update?code=000001')
        self.assertEqual(caught.exception.code, 404)
        with urlopen(base + '/api/tasks') as response:
            self.assertIsNone(json.load(response)['latest'])


if __name__ == '__main__':
    unittest.main()
