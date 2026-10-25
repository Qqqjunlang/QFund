"""网页分析整合：复用图表、只读快照与嵌入安全边界。"""
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from fundlab.config import AppConfig
from fundlab.errors import FundlabError
from fundlab.parsers.history import parse_history
from fundlab.services.analysis import premium_chart_document
from fundlab.storage.repository import Repository
from fundlab.web.premium import chart_document, chart_policy
from fundlab.web.server import make_server
from test_data_storage import FUND, STAMP, fixture_row


class WebAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = AppConfig(Path(self.temp.name))
        self.repo = Repository(self.config.database_path)
        self.repo.start_run("web-analysis", FUND.code, "initial")
        self.repo.commit_window(FUND, parse_history([fixture_row(), fixture_row("2020-01-03", discount_rt="-")], FUND.code, STAMP),
                                "web-analysis", {"full_history_verified": False})

    def start_server(self, config=None):
        server = make_server(config or self.config, 0)
        Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    def test_document_is_current_readonly_and_does_not_save_chart(self):
        before = self.config.database_path.read_bytes()
        runs = self.repo.list_runs()
        document = chart_document(self.config, FUND.code)
        self.assertIn('19.96%', document)
        self.assertIn('2020-01-02', document)
        self.assertIn('fundlab-premium', document)
        self.assertIn("parent.postMessage", document)
        self.assertFalse((self.config.data_dir / 'analysis').exists())
        self.assertFalse(self.config.funds_dir.exists())
        self.assertEqual(self.config.database_path.read_bytes(), before)
        self.assertEqual(self.repo.list_runs(), runs)

    def test_offline_and_embedded_share_final_controls(self):
        offline = premium_chart_document(self.config, FUND.code)
        embedded = premium_chart_document(self.config, FUND.code, embedded=True)
        for document in (offline, embedded):
            for text in ('rank-premium', 'average-periods', '恢复当前溢价', "['30','50','70']", 'minallowed', 'maxallowed'):
                self.assertIn(text, document)
            self.assertNotIn('<script src=', document)
            self.assertNotIn('五档', document)
        self.assertNotIn('main{max-width:none;margin:0;', offline)
        self.assertIn('main{max-width:none;margin:0;', embedded)

    def test_chart_policy_hashes_inline_scripts_instead_of_allowing_arbitrary_scripts(self):
        document = chart_document(self.config, FUND.code)
        policy = chart_policy(document)
        scripts = policy.split('script-src ', 1)[1].split(';', 1)[0]
        self.assertGreaterEqual(scripts.count('sha256-'), 5)
        self.assertNotIn('unsafe-inline', scripts)
        self.assertNotIn('unsafe-eval', scripts)
        self.assertIn("connect-src 'none'", policy)
        self.assertIn("frame-ancestors 'self'", policy)

    def test_fund_name_is_not_executable_markup(self):
        with self.repo.connection() as db:
            db.execute("UPDATE funds SET name=?", ('<script>alert(1)</script>',))
        document = chart_document(self.config, FUND.code)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', document)
        self.assertNotIn('<script>alert(1)</script>', document)

    def test_http_analysis_document_and_parent_sandbox(self):
        base = self.start_server()
        with urlopen(base + '/analysis/premium?code=000001') as response:
            document = response.read().decode('utf-8')
            self.assertEqual(response.headers['Content-Security-Policy'], chart_policy(document))
            self.assertIn('fundlab-premium', document)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        with urlopen(base + '/app.js') as response:
            script = response.read().decode('utf-8')
            self.assertIn("'allow-scripts allow-downloads allow-forms'", script)
            self.assertNotIn('allow-same-origin', script)
            self.assertIn('event.source!==activeFrame.contentWindow', script)

    def test_http_missing_invalid_and_extra_parameters_return_useful_error(self):
        base = self.start_server()
        for query, status in (('code=999999', 404), ('code=abc', 400), ('code=000001&code=513100', 400), ('code=000001&file=secret', 400)):
            with self.subTest(query=query), self.assertRaises(HTTPError) as caught:
                urlopen(base + '/analysis/premium?' + query)
            self.assertEqual(caught.exception.code, status)
            self.assertIn('fundlab-premium', caught.exception.read().decode('utf-8'))

    def test_missing_database_is_not_created_and_old_saved_html_not_used(self):
        config = AppConfig(self.config.root / 'no-database')
        with self.assertRaises(FundlabError):
            premium_chart_document(config, FUND.code)
        self.assertFalse(config.root.exists())
        directory = self.config.data_dir / 'analysis' / '000001_测试专用基金'
        directory.mkdir(parents=True)
        old = directory / 'premium_timeline.html'
        old.write_text('OLD_CHART_SENTINEL', encoding='utf-8')
        self.assertNotIn('OLD_CHART_SENTINEL', chart_document(self.config, FUND.code))
        self.assertEqual(old.read_text(encoding='utf-8'), 'OLD_CHART_SENTINEL')

    def test_second_server_cannot_share_first_servers_port(self):
        with make_server(self.config, 0) as first:
            with self.assertRaises(OSError):
                make_server(self.config, first.server_port)


if __name__ == '__main__':
    unittest.main()
