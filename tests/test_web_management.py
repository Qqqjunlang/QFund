"""HTTP coverage for the new browser entry points and shared write protection."""

from base64 import b64encode
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest import TestCase
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from fundlab.config import AppConfig
from fundlab.web.server import make_server


class WebManagementTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = AppConfig(Path(directory.name))
        server = make_server(self.config, 0)
        self.addCleanup(server.server_close)
        self.origin = f'http://127.0.0.1:{server.server_port}'
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        _, session = self.request('/api/session')
        self.token = session['csrf_token']

    def request(self, path, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        defaults = {} if data is None else {
            'Content-Type': 'application/json', 'Origin': self.origin, 'X-Fundlab-CSRF': self.token,
        }
        request = Request(self.origin + path, data=data, headers={**defaults, **(headers or {})})
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def import_payload(self):
        return {'code': '000001', 'name': '测试基金', 'filename': 'test.csv',
                'content': b64encode('价格日期,收盘价\n2020-01-02,2\n'.encode()).decode()}

    def test_import_and_maintenance_flow(self):
        status, preview = self.request('/api/import/preview', self.import_payload())
        self.assertEqual((status, preview['inserted']), (200, 1))
        self.assertFalse(self.config.database_path.exists())
        status, result = self.request('/api/import/commit', {'token': preview['token']})
        self.assertEqual((status, result['inserted']), (200, 1))
        _, history = self.request('/api/history?code=000001')
        meta = history['row_meta'][0]
        self.assertEqual(meta['record_origin'], 'import')
        edit = {'code': '000001', 'day': '2020-01-02', 'action': 'edit',
                'expected_revision': meta['revision'], 'changes': {'close': '3'}}
        status, state = self.request('/api/maintenance/row', edit)
        self.assertEqual(status, 200)
        self.assertEqual(self.request('/api/maintenance/row', edit)[0], 409)
        _, history = self.request('/api/history?code=000001')
        self.assertEqual(history['rows'][0]['close'], '3')
        self.assertEqual(history['row_meta'][0]['protected_fields'], ['close'])
        status, groups = self.request('/api/groups/change', {'action': 'create', 'name': '测试组'})
        self.assertEqual(status, 200)
        group = groups['groups'][0]
        self.assertEqual(self.request('/api/groups/change', {
            'action': 'members', 'group_id': group['id'], 'expected_revision': group['revision'],
            'codes': ['000001'],
        })[0], 200)
        status, trashed = self.request('/api/maintenance/fund', {
            'code': '000001', 'action': 'trash', 'expected_revision': state['revision'],
        })
        self.assertEqual(status, 200)
        self.assertEqual(self.request('/api/trash')[1][0]['code'], '000001')
        self.assertEqual(self.request('/api/groups')[1][0]['codes'], [])
        self.assertEqual(self.request('/api/maintenance/fund', {
            'code': '000001', 'action': 'restore', 'expected_revision': trashed['revision'],
        })[0], 200)
        self.assertEqual(self.request('/api/history?code=000001')[1]['rows'][0]['close'], '3')

    def test_import_routes_share_csrf_and_reject_invalid_shapes(self):
        payload = self.import_payload()
        for headers in ({'X-Fundlab-CSRF': ''}, {'Origin': 'https://example.invalid'}):
            self.assertEqual(self.request('/api/import/preview', payload, headers)[0], 403)
        self.assertEqual(self.request('/api/import/preview', {**payload, 'content': 'bad!'})[0], 400)
        self.assertEqual(self.request('/api/import/preview', {**payload, 'extra': 1})[0], 400)
        self.assertEqual(self.request('/api/maintenance?code=000001&code=000002')[0], 400)
        self.assertFalse(self.config.database_path.exists())
