"""名称选择边界；人工候选只用于测试。"""
import unittest
from unittest.mock import patch
from fundlab.config import AppConfig
from fundlab.errors import FundlabError
from fundlab.services.search import resolve_name


class SearchTests(unittest.TestCase):
    def test_multiple_candidates_require_explicit_code(self):
        report = {"matches": [{"code": "000001", "name": "测试甲", "category": "QDII"},
                               {"code": "000002", "name": "测试乙", "category": "QDII"}]}
        with patch("fundlab.services.search.verify_catalog", return_value=report), self.assertRaises(FundlabError) as error:
            resolve_name(AppConfig.default(), "测试")
        self.assertEqual(error.exception.code, "AMBIGUOUS_FUND")

    def test_incomplete_sources_do_not_claim_unique_name(self):
        report = {"matches": [{"code": "000001", "name": "测试", "category": "QDII"}], "missing_sources": ["/fixture"]}
        with patch("fundlab.services.search.verify_catalog", return_value=report), self.assertRaises(FundlabError) as error:
            resolve_name(AppConfig.default(), "测试")
        self.assertEqual(error.exception.code, "CATALOG_INCOMPLETE")

    def test_unique_name_resolves_and_does_not_overwrite_phase1_evidence(self):
        report = {"matches": [{"code": "000001", "name": "测试", "category": "QDII"}], "missing_sources": []}
        with patch("fundlab.services.search.verify_catalog", return_value=report) as lookup:
            self.assertEqual(resolve_name(AppConfig.default(), "测试"), "000001")
        self.assertFalse(lookup.call_args.kwargs["persist_evidence"])
