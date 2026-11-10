"""Checks for price window semantics and its protected HTTP entry point."""

from decimal import Decimal
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest import TestCase
from urllib.error import HTTPError
from urllib.request import urlopen

from fundlab.analysis.price import closing_prices, parse_periods, price_percentiles, summarize_prices
from fundlab.config import AppConfig
from fundlab.errors import FundlabError
from fundlab.services.data_import import DataImportService
from fundlab.web.server import make_server


class PriceAnalysisTests(TestCase):
    def test_price_summary_uses_global_latest_and_counts_equal_values_in_selected_window(self):
        rows = [{'price_date': f'2026-01-{index:02d}', 'close': value}
                for index, value in enumerate(('0', '2', '2', None, '10', None), 1)]
        current = summarize_prices(rows, end='2026-01-03')
        self.assertEqual((current['latest_price'], current['latest_date'], current['records']), ('10', '2026-01-05', 6))
        self.assertEqual(current['comparison']['percentile_rank'], '100')
        tied = summarize_prices(rows, comparison_price='2')
        self.assertEqual(tied['comparison'], {'mode': 'custom', 'price': '2', 'percentile_rank': '75'})
        self.assertEqual(summarize_prices(rows, comparison_price='0')['comparison']['percentile_rank'], '25')
        self.assertEqual(summarize_prices(rows, comparison_price='1.999999999999999999999999999999999999999')['comparison']['percentile_rank'], '25')
        empty = summarize_prices(rows, start='2026-01-04', end='2026-01-04', comparison_price='2')
        self.assertIsNone(empty['comparison']['percentile_rank'])
        self.assertEqual(empty['valid_samples'], 0)
        self.assertIsNone(summarize_prices([rows[3]])['latest_price'])

    def test_price_summary_rejects_invalid_prices_and_dates(self):
        rows = [{'price_date': '2026-01-01', 'close': '2'}]
        for value in ('', 'NaN', 'Infinity', '-1', '2%', '1,2', '1e1000', True, []):
            with self.subTest(value=value), self.assertRaises(FundlabError):
                summarize_prices(rows, comparison_price=value)
        with self.assertRaises(FundlabError):
            summarize_prices(rows, start='2026-02-30')

    def test_price_summary_http_is_read_only_and_checks_parameters(self):
        with TemporaryDirectory() as directory:
            config = AppConfig(Path(directory))
            importer = DataImportService(config)
            content = '价格日期,收盘价\n2026-01-01,0\n2026-01-02,2\n2026-01-03,\n'.encode()
            importer.commit(importer.preview('000001', '样本', 'prices.csv', content)['token'])
            before = config.database_path.read_bytes()
            with make_server(config, 0) as server:
                thread = Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    url = f'http://127.0.0.1:{server.server_port}/api/price-summary?code=000001'
                    with urlopen(url + '&price=0') as response:
                        result = json.load(response)
                    self.assertEqual((result['latest_price'], result['latest_date']), ('2', '2026-01-02'))
                    self.assertEqual(result['comparison']['percentile_rank'], '50')
                    for query, status in (('&price=-1', 400), ('&price=', 400), ('&bad=1', 400),
                                          ('&price=1&price=2', 400), ('&start=2026-02-01', 404)):
                        with self.subTest(query=query), self.assertRaises(HTTPError) as caught:
                            urlopen(url + query)
                        caught.exception.close()
                        self.assertEqual(caught.exception.code, status)
                finally:
                    server.shutdown()
                    thread.join()
            self.assertEqual(config.database_path.read_bytes(), before)

    def test_means_use_prior_valid_samples_and_preserve_zero_and_gaps(self):
        rows = [{'price_date': f'2026-01-{index:02d}', 'close': close}
                for index, close in enumerate(('1', '2', None, '0', '5', None, '7', '1000'), 1)]
        points = closing_prices(reversed(rows), (2, 5), start='2026-01-04', end='2026-01-07')
        self.assertEqual([(p.close, p.averages) for p in points], [
            (Decimal(0), (Decimal(1), None)),
            (Decimal(5), (Decimal('2.5'), None)),
            (None, (None, None)),
            (Decimal(7), (Decimal(6), Decimal(3))),
        ])

    def test_validation_rejects_invalid_periods_or_dates_and_empty_intervals(self):
        self.assertEqual(parse_periods('5， 20, 5'), (5, 20))
        self.assertEqual(parse_periods(''), (5,))
        for text in ('0', '-1', '1.5', '10001', '1,2,3,4,5,6', '5,'):
            with self.subTest(text=text), self.assertRaises(FundlabError):
                parse_periods(text)
        rows = [{'price_date': '2026-01-01', 'close': '2'}]
        for start, end, code in (('2026-02-30', '', 'VALIDATION_ERROR'),
                                 ('2026-01-02', '2026-01-01', 'VALIDATION_ERROR'),
                                 ('2026-01-02', '', 'NO_DATA')):
            with self.assertRaises(FundlabError) as caught:
                closing_prices(rows, (2,), start=start, end=end)
            self.assertEqual(caught.exception.code, code)

    def test_reference_percentiles_use_only_valid_prices_in_the_selected_range(self):
        rows = [{'price_date': f'2026-01-{index:02d}', 'close': value}
                for index, value in enumerate(('100', '0', None, '20', '1000'), 1)]
        points = closing_prices(rows, (2,), start='2026-01-02', end='2026-01-04')
        self.assertEqual(price_percentiles(points), {30: Decimal(6), 50: Decimal(10), 70: Decimal(14)})
        empty = closing_prices(rows, (2,), start='2026-01-03', end='2026-01-03')
        self.assertEqual(price_percentiles(empty), {30: None, 50: None, 70: None})

    def test_price_route_renders_selected_options_and_remains_read_only(self):
        with TemporaryDirectory() as directory:
            config = AppConfig(Path(directory))
            importer = DataImportService(config)
            content = '价格日期,收盘价\n2026-01-01,2\n2026-01-02,4\n'.encode()
            token = importer.preview('000001', '<script>样本</script>', 'prices.csv', content)['token']
            importer.commit(token)
            before = config.database_path.read_bytes()
            with make_server(config, 0) as server:
                thread = Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    url = f'http://127.0.0.1:{server.server_port}/analysis/price?code=000001'
                    with urlopen(url + '&start=2026-01-02&periods=2', timeout=10) as response:
                        chart = response.read().decode()
                        self.assertIn('fundlab-price', chart)
                        self.assertIn('&lt;script&gt;样本&lt;/script&gt;', chart)
                        self.assertIn('2026-01-02', chart)
                        self.assertIn("frame-ancestors 'self'", response.headers['Content-Security-Policy'])
                    for query, status in (('&periods=0', 400), ('&start=2026-01-03', 404), ('&bad=1', 400)):
                        with self.assertRaises(HTTPError) as caught:
                            urlopen(url + query, timeout=10)
                        with caught.exception as response:
                            self.assertEqual(response.code, status)
                            self.assertIn('fundlab-price', response.read().decode())
                finally:
                    server.shutdown()
                    thread.join()
            self.assertEqual(config.database_path.read_bytes(), before)
            self.assertFalse((config.data_dir / 'analysis').exists())
