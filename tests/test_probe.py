"""仅验证隐私边界和会话互斥，不使用假行情代替真实采集。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from fundlab.collectors.session import ProfileLock
from fundlab.config import AppConfig
from fundlab.errors import SessionBusy
from fundlab.probe import check_session, response_shape, safe_url
from fundlab.verification import page_fingerprint, validate_rows
from fundlab.errors import FundlabError


class ProbeTests(unittest.TestCase):
    def test_query_values_are_not_recorded(self):
        result = safe_url("https://www.jisilu.cn/data/test/?token=SECRET&page=1")
        self.assertNotIn("SECRET", str(result))
        self.assertEqual(result["query_keys"], ["page", "token"])

    def test_payload_values_are_not_recorded(self):
        shape = response_shape({"token": "SECRET", "rows": [{"cell": {"price": "1"}}]})
        self.assertNotIn("SECRET", str(shape))
        self.assertEqual(shape["cell_keys"], ["price"])
        self.assertEqual(shape["row_count"], 1)

    def test_profile_lock_blocks_second_owner_and_releases(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "profile.lock"
            with ProfileLock(path):
                with self.assertRaises(SessionBusy):
                    with ProfileLock(path):
                        pass
            with ProfileLock(path):
                pass

    def test_project_root_is_independent_of_working_directory(self):
        self.assertTrue((AppConfig.default().root / "pyproject.toml").is_file())

    def test_repeated_page_has_same_fingerprint(self):
        # 这是人工构造的分页测试输入，不是运行时基金行情。
        first = [{"id": "fixture", "cell": {"fund_id": "513100", "price_dt": "2020-01-01"}}]
        reordered_keys = [{"cell": {"price_dt": "2020-01-01", "fund_id": "513100"}, "id": "fixture"}]
        self.assertEqual(page_fingerprint(first), page_fingerprint(reordered_keys))

    def test_wrong_fund_is_rejected(self):
        with self.assertRaises(FundlabError):
            validate_rows({"rows": [{"id": "fixture", "cell": {"fund_id": "OTHER", "price_dt": "2020-01-01"}}]})

    def test_missing_rows_is_not_empty_terminus(self):
        with self.assertRaises(FundlabError):
            validate_rows({"error": "fixture"})

    def test_login_redirect_is_not_visible_history(self):
        page = MagicMock()
        page.url = "https://www.jisilu.cn/account/login/fixture"
        self.assertEqual(check_session(page), "AUTH_REQUIRED")
        page.locator.assert_not_called()

    def test_history_headers_without_rows_are_not_success(self):
        page = self._history_page([])
        self.assertEqual(check_session(page), "UNVERIFIED")

    def test_unrelated_table_is_not_success(self):
        page = self._history_page([])
        page.locator.return_value.count.return_value = 0
        self.assertEqual(check_session(page), "UNVERIFIED")

    def test_error_row_is_not_history(self):
        page = self._history_page([["加载失败"]])
        self.assertEqual(check_session(page), "UNVERIFIED")

    def test_invalid_date_is_not_history(self):
        page = self._history_page([["2020-02-30"] + ["fixture"] * 9])
        self.assertEqual(check_session(page), "UNVERIFIED")

    def test_loaded_dated_history_is_visible(self):
        # 人工构造 UI 判断输入，不使用它作为运行时行情。
        page = self._history_page([["2020-01-01"] + ["fixture"] * 9])
        self.assertEqual(check_session(page), "HISTORY_VISIBLE")

    @staticmethod
    def _history_page(rows):
        page = MagicMock()
        page.url = "https://www.jisilu.cn/data/qdii/detail/513100"
        table = page.locator.return_value
        table.count.return_value = 1
        def child(selector):
            element = MagicMock()
            if selector == "th":
                element.all_text_contents.return_value = ["价格日期", "净值", "溢价率"]
            elif selector == "tbody tr":
                element.evaluate_all.return_value = rows
            return element
        table.locator.side_effect = child
        return page


if __name__ == "__main__":
    unittest.main()
