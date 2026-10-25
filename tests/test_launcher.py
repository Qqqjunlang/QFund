"""启动、复用、冲突与就绪检查；不启动真实采集或修改本地行情。"""
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import urlopen

from fundlab.config import AppConfig
from fundlab.errors import FundlabError
from fundlab.launcher import check_environment, existing_service, launch, open_workbench
from fundlab.web.identity import service_identity
from fundlab.web.server import make_server


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = AppConfig(Path(self.temp.name))

    def start_server(self):
        server = make_server(self.config, 0)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 3)
        self.addCleanup(server.shutdown)
        return server

    def test_health_identifies_same_project_without_creating_database(self):
        server = self.start_server()
        health = existing_service(self.config, server.server_port)
        self.assertEqual(health['app'], 'fundlab-local')
        self.assertEqual(health['pid'], os.getpid())
        self.assertNotIn(str(self.config.root), json.dumps(health))
        other = AppConfig(self.config.root / 'other-project')
        self.assertIsNone(existing_service(other, server.server_port))
        self.assertFalse(self.config.database_path.exists())
        with urlopen(f'http://127.0.0.1:{server.server_port}/api/health') as response:
            self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_repeat_start_reuses_actual_server_and_opens_only_its_url(self):
        server = self.start_server()
        with patch('fundlab.launcher.open_workbench', return_value=True) as opened, patch(
                'fundlab.web.server.make_server', side_effect=AssertionError('重复启动不得建服务')):
            self.assertEqual(launch(self.config, server.server_port), 'reused')
            self.assertEqual(launch(self.config, server.server_port), 'reused')
        self.assertEqual(opened.call_count, 2)
        opened.assert_called_with(f'http://127.0.0.1:{server.server_port}/')
        self.assertFalse(self.config.database_path.exists())

    def test_occupied_port_for_another_project_is_not_opened_or_killed(self):
        server = self.start_server()
        other = AppConfig(self.config.root / 'other-project')
        with patch('fundlab.launcher.open_workbench') as opened, self.assertRaises(FundlabError) as caught:
            launch(other, server.server_port, startup_timeout=.1)
        self.assertEqual(caught.exception.code, 'PORT_IN_USE')
        opened.assert_not_called()
        self.assertIsNotNone(existing_service(self.config, server.server_port))
        self.assertFalse(other.root.exists())

    def test_unknown_http_content_is_not_reused(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        class Unrelated(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"app":"another-program"}')
            def log_message(self, *args):
                pass
        with ThreadingHTTPServer(('127.0.0.1', 0), Unrelated) as server:
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                self.assertIsNone(existing_service(self.config, server.server_port))
                with patch('fundlab.launcher.open_workbench') as opened, self.assertRaises(FundlabError):
                    launch(self.config, server.server_port, startup_timeout=.1)
                opened.assert_not_called()
            finally:
                server.shutdown()
                worker.join(3)

    def test_simultaneous_start_can_reuse_winner_after_port_race(self):
        health = {**service_identity(self.config), 'status': 'ready', 'pid': 123}
        with patch('fundlab.launcher.existing_service', side_effect=[None, health]), patch(
                'fundlab.web.server.make_server', side_effect=OSError(10048, '测试端口竞态')), patch(
                'fundlab.launcher.open_workbench', return_value=True) as opened:
            self.assertEqual(launch(self.config), 'reused')
        opened.assert_called_once_with('http://127.0.0.1:8765/')

    def test_environment_failure_does_not_bind_or_open(self):
        with patch('fundlab.launcher.importlib.import_module', side_effect=ImportError('测试缺依赖')):
            with self.assertRaises(FundlabError) as caught:
                check_environment()
        self.assertEqual(caught.exception.code, 'DEPENDENCY_MISSING')
        self.assertIn('playwright', str(caught.exception))
        with patch('fundlab.launcher.check_environment', side_effect=caught.exception), patch(
                'fundlab.web.server.make_server') as make, patch('fundlab.launcher.open_workbench') as opened:
            with self.assertRaises(FundlabError):
                launch(self.config)
        make.assert_not_called()
        opened.assert_not_called()

    def test_not_ready_does_not_open_browser_and_releases_port(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        with patch('fundlab.launcher.existing_service', return_value=None), patch(
                'fundlab.launcher.open_workbench') as opened, self.assertRaises(FundlabError) as caught:
            launch(self.config, port, startup_timeout=.05)
        self.assertEqual(caught.exception.code, 'START_TIMEOUT')
        opened.assert_not_called()
        with make_server(self.config, port):
            pass

    def test_fresh_start_waits_until_ready_and_ctrl_c_preserves_active_task(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        real_join = Thread.join
        def interrupted_join(worker, timeout=None):
            if timeout == .4:
                raise KeyboardInterrupt()
            return real_join(worker, timeout)
        out = StringIO()
        def opened_after_ready(url):
            self.assertIsNotNone(existing_service(self.config, port))
            return True
        with patch('fundlab.launcher.open_workbench', side_effect=opened_after_ready) as opened, patch(
                'fundlab.web.tasks.TaskManager.state', side_effect=[{'active': {'kind': 'update'}}, {'active': None}]), patch(
                'fundlab.launcher.Thread.join', interrupted_join), redirect_stdout(out):
            self.assertEqual(launch(self.config, port), 'stopped')
        opened.assert_called_once_with(f'http://127.0.0.1:{port}/')
        self.assertIn('还有任务进行中', out.getvalue())
        with make_server(self.config, port):
            pass

    def test_browser_failure_keeps_reused_service_available_with_manual_url(self):
        server = self.start_server()
        out = StringIO()
        with patch('fundlab.launcher.open_workbench', return_value=False), redirect_stdout(out):
            self.assertEqual(launch(self.config, server.server_port), 'reused')
        self.assertIn('请手动访问', out.getvalue())
        self.assertIsNotNone(existing_service(self.config, server.server_port))

    def test_edge_opens_url_as_argument_and_falls_back_on_failure(self):
        url = 'http://127.0.0.1:8765/'
        with patch('fundlab.collectors.session.find_edge', return_value=Path('C:/Program Files/测试 Edge/msedge.exe')), patch(
                'fundlab.launcher.subprocess.Popen') as popen:
            self.assertTrue(open_workbench(url))
        self.assertEqual(popen.call_args.args[0][-2:], ['--new-tab', url])
        self.assertNotIn('shell', popen.call_args.kwargs)
        with patch('fundlab.collectors.session.find_edge', return_value=None), patch(
                'fundlab.launcher.webbrowser.open', return_value=False):
            self.assertFalse(open_workbench(url))

    def test_health_probe_ignores_proxy_and_refuses_redirect(self):
        with patch('fundlab.launcher.build_opener') as build:
            build.return_value.open.side_effect = URLError('测试连接失败')
            self.assertIsNone(existing_service(self.config, 8765))
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsNone(build.call_args.args[1].redirect_request(None, None, 302, '', {}, 'https://example.invalid/'))

    def test_second_port_cannot_replace_active_projects_task_records(self):
        with make_server(self.config, 0) as first:
            state = [{'id': 'test-existing', 'kind': 'update', 'status': 'running',
                      'request_id': 'request-existing-123456', 'payload': {'code': '000001'}}]
            record = self.config.local_dir / 'web-tasks.json'
            record.write_text(json.dumps(state), encoding='utf-8')
            original = record.read_bytes()
            with self.assertRaises(FundlabError) as caught:
                make_server(self.config, 0)
            self.assertEqual(caught.exception.code, 'SERVER_BUSY')
            self.assertEqual(record.read_bytes(), original)
        # 关闭原实例释放项目锁；恢复规则仍如实将上次未结束任务标为待确认。
        with make_server(self.config, 0) as restarted:
            self.assertEqual(restarted.task_manager.state()['latest']['status'], 'interrupted')

    def test_invalid_port_cannot_start(self):
        for port in (0, -1, 65536):
            with self.assertRaises(FundlabError):
                launch(self.config, port)

    @unittest.skipUnless(os.name == 'nt', 'Windows双击入口')
    def test_cmd_handles_chinese_space_path_and_missing_environment(self):
        script = AppConfig.default().root / '启动工作台.cmd'
        raw = script.read_bytes()
        self.assertNotIn(b'\n', raw.replace(b'\r\n', b''))
        directory = self.config.root / '中文 空格 项目'
        directory.mkdir()
        target = directory / script.name
        target.write_bytes(raw)
        outcome = subprocess.run([os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', str(target)],
                                 input='\n', text=True, encoding='utf-8', capture_output=True, cwd=self.config.root, timeout=10)
        self.assertEqual(outcome.returncode, 1)
        self.assertIn('未找到项目Python环境', outcome.stdout)


if __name__ == '__main__':
    unittest.main()
