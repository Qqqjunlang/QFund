"""Packaged browser compatibility reaches the host and isolated/offline charts."""
import base64
import hashlib
from html.parser import HTMLParser
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from fundlab.analysis.charts import render_premium_timeline
from fundlab.analysis.premium import premium_series
from fundlab.analysis.price import closing_prices
from fundlab.analysis.price_chart import render_price_timeline
from fundlab.models import Fund
from fundlab.config import AppConfig
from fundlab.presentation import browser_compat_script
from fundlab.web.premium import chart_policy
from fundlab.web.server import ASSETS, make_server


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.scripts = []
        self.parts = None

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.parts = []

    def handle_data(self, data):
        if self.parts is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.parts is not None:
            self.scripts.append(''.join(self.parts).replace('\r\n', '\n').replace('\r', '\n'))
            self.parts = None


class BrowserResourceTests(unittest.TestCase):
    def test_host_serves_compat_before_consumers_with_static_boundary(self):
        # Git's Windows checkout can use CRLF for JS; inline read_text uses LF.
        # Exercise both independently of the developer's checkout settings.
        script = browser_compat_script().replace('\r\n', '\n').replace('\r', '\n')
        with tempfile.TemporaryDirectory() as directory:
            assets = Path(directory)
            (assets / 'index.html').write_bytes((ASSETS / 'index.html').read_bytes())
            for newline in ('\n', '\r\n'):
                expected = script.replace('\n', newline).encode('utf-8')
                (assets / 'browser_compat.js').write_bytes(expected)
                with self.subTest(newline=repr(newline)), patch('fundlab.web.server.ASSETS', assets):
                    self._assert_host_compat_static_boundary(expected)

    def _assert_host_compat_static_boundary(self, expected):
        with tempfile.TemporaryDirectory() as directory:
            with make_server(AppConfig(Path(directory)), 0) as server:
                thread = Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    base = f'http://127.0.0.1:{server.server_port}'
                    with urlopen(base + '/') as response:
                        host = response.read().decode('utf-8')
                        self.assertIn("script-src 'self'", response.headers['Content-Security-Policy'])
                    self.assertLess(host.index('browser_compat.js'), host.index('management.js'))
                    self.assertLess(host.index('browser_compat.js'), host.index('app.js'))
                    with urlopen(base + '/browser_compat.js') as response:
                        served = response.read()
                        self.assertEqual(served, expected)
                        self.assertEqual(served.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n'),
                                         browser_compat_script().replace('\r\n', '\n').replace('\r', '\n'))
                        self.assertIn('javascript', response.headers['Content-Type'])
                    for path in ('/static/browser_compat.js', '/presentation.py'):
                        with self.assertRaises(HTTPError) as caught:
                            urlopen(base + path)
                        self.assertEqual(caught.exception.code, 404)
                finally:
                    server.shutdown()
                    thread.join()

    def test_self_contained_charts_run_compat_first_and_hash_every_script(self):
        fund = Fund('000001', '测试基金', 'QDII', 'https://example.invalid/test')
        rows = [{'price_date': '2026-01-01', 'source_premium': '0.01', 'close': '2'}]
        # Price input uses the public analysis contract, independently of storage.
        for document in (
            render_premium_timeline(fund, premium_series(rows)),
            render_premium_timeline(fund, premium_series(rows), embedded=True),
            render_price_timeline(fund, closing_prices(rows, (5,)), (5,)),
        ):
            scripts = Scripts()
            scripts.feed(document)
            self.assertEqual(scripts.scripts[0], browser_compat_script().replace('\r\n', '\n'))
            self.assertNotIn('<script src=', document)
            policy = chart_policy(document)
            script_policy = policy.split('script-src ', 1)[1].split(';', 1)[0]
            for script in scripts.scripts:
                digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
                self.assertIn("'sha256-" + digest + "'", script_policy)
            self.assertNotIn('unsafe-inline', script_policy)
            self.assertNotIn('unsafe-eval', script_policy)

