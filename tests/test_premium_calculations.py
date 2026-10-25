"""可手算的分析样本，仅用于验证公式，不作为运行行情。"""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
import json
import unittest

from fundlab.analysis.calculations import AnalysisRequest, analyze_premium
from fundlab.analysis.premium import PremiumPoint, PremiumSeries
from fundlab.errors import FundlabError


def sample(values, start="2020-01-01"):
    first = date.fromisoformat(start)
    return PremiumSeries(tuple(PremiumPoint((first + timedelta(days=i)).isoformat(), None if value is None else str(value))
                               for i, value in enumerate(values)))


class PremiumCalculationTests(unittest.TestCase):
    def test_custom_comparison_does_not_change_current_or_history(self):
        series = sample([1, 2, 2, 3, 2])
        default = analyze_premium(series)
        result = analyze_premium(series, AnalysisRequest(comparison_premium="001.500"))
        self.assertEqual(result["comparison"], {"mode": "custom", "premium": "1.5", "price_date": None, "percentile_rank": "20"})
        for key in ("current", "series", "window", "quantiles"):
            self.assertEqual(result[key], default[key])
        self.assertEqual(default["comparison"]["mode"], "current")
        self.assertEqual(default["comparison"]["percentile_rank"], default["current"]["percentile_rank"])

    def test_custom_comparison_supports_zero_negative_and_outside_range(self):
        for target, expected in (("-3", "0"), ("-1", "25"), ("0", "75"), ("+0.0", "75"), ("3", "100")):
            with self.subTest(target=target):
                result = analyze_premium(sample([-2, 0, 0, 2]), AnalysisRequest(comparison_premium=target))
                self.assertEqual(result["comparison"]["percentile_rank"], expected)
                self.assertEqual(result["window"]["valid_samples"], 4)

    def test_custom_decimal_boundary_and_empty_reference(self):
        series = sample([1, 2])
        before = analyze_premium(series, AnalysisRequest(comparison_premium="1.9999999999999999999999999999"))
        equal = analyze_premium(series, AnalysisRequest(comparison_premium="2"))
        self.assertEqual(before["comparison"]["percentile_rank"], "50")
        self.assertEqual(equal["comparison"]["percentile_rank"], "100")
        empty = analyze_premium(series, AnalysisRequest("custom", "2019-01-01", "2019-02-01", comparison_premium="2"))
        self.assertIsNone(empty["comparison"]["percentile_rank"])

    def test_invalid_custom_premium_has_clear_error(self):
        for value in ("", " ", "NaN", "Infinity", "abc", "12.5%", "1e2", "9" * 400, True, 12.5):
            with self.subTest(value=value), self.assertRaises(FundlabError) as caught:
                analyze_premium(sample([1]), AnalysisRequest(comparison_premium=value))
            self.assertEqual(caught.exception.code, "VALIDATION_ERROR")

    def test_linear_quantiles_can_be_calculated_by_hand(self):
        result = analyze_premium(sample([1, 2, 3, 4, 5]))
        self.assertEqual(result["quantiles"], {"10": "1.4", "30": "2.2", "50": "3", "70": "3.8", "90": "4.6"})
        self.assertEqual(result["current"]["percentile_rank"], "100")

    def test_rank_includes_ties(self):
        result = analyze_premium(sample([1, 2, 2, 3, 2]))
        self.assertEqual(result["current"]["percentile_rank"], "80")
        self.assertEqual(result["window"]["valid_samples"], 5)

    def test_current_outside_reference_does_not_change_denominator(self):
        result = analyze_premium(sample([1, 4, 5, 9, 3]), AnalysisRequest("custom", "2020-01-01", "2020-01-03"))
        self.assertEqual(result["current"]["price_date"], "2020-01-05")
        self.assertEqual(result["current"]["premium"], "3")
        self.assertEqual(result["window"]["valid_samples"], 3)
        self.assertLess(abs(Decimal(result["current"]["percentile_rank"]) - Decimal(100) / 3), Decimal("1e-25"))
        self.assertEqual(result["quantiles"]["50"], "4")

    def test_average_uses_preceding_window_but_not_future(self):
        result = analyze_premium(sample([1, 2, 3, 4, 5]), AnalysisRequest("custom", "2020-01-03", "2020-01-04", (3,)))
        self.assertEqual([row["average_premiums"]["3"] for row in result["series"]], ["2", "3"])
        self.assertEqual(result["current"]["average_premiums"]["3"], "4")

    def test_missing_stays_missing_and_averages_count_valid_observations(self):
        result = analyze_premium(sample([1, None, 3, 5, None]), AnalysisRequest(ma_periods=(2,)))
        self.assertEqual([row["average_premiums"]["2"] for row in result["series"]], [None, None, "2", "4", None])
        self.assertEqual(result["window"]["valid_samples"], 3)
        self.assertEqual(result["window"]["missing_samples"], 2)
        self.assertEqual(result["window"]["anchor_date"], "2020-01-05")
        self.assertEqual(result["current"]["price_date"], "2020-01-04")
        self.assertIn("CURRENT_DATE_DIFFERS", {w["code"] for w in result["warnings"]})

    def test_insufficient_average_is_not_a_shorter_mean(self):
        result = analyze_premium(sample(list(range(50)) + [None]))
        self.assertIsNotNone(result["current"]["average_premiums"]["20"])
        self.assertIsNone(result["current"]["average_premiums"]["60"])
        self.assertTrue(all(row["average_premiums"]["60"] is None for row in result["series"]))
        warning = next(w for w in result["warnings"] if w["code"] == "AVERAGE_INSUFFICIENT")
        self.assertEqual((warning["period"], warning["available"]), (60, 50))

    def test_negative_and_zero_premiums_are_valid(self):
        result = analyze_premium(sample([-2, 0, -1, 0]), AnalysisRequest(ma_periods=(1, 2)))
        self.assertEqual(result["window"]["valid_samples"], 4)
        self.assertEqual(result["current"]["average_premiums"]["2"], "-0.5")
        self.assertEqual(result["current"]["average_premiums"]["1"], "0")

    def test_single_observation_has_same_five_reference_values(self):
        result = analyze_premium(sample(["1.25"]))
        self.assertEqual(set(result["quantiles"].values()), {"1.25"})
        self.assertEqual(result["current"]["percentile_rank"], "100")

    def test_month_end_and_leap_year_use_calendar_months(self):
        for anchor, period, expected in (("2020-03-31", "1m", "2020-02-29"),
                                         ("2021-03-31", "1m", "2021-02-28"),
                                         ("2020-02-29", "1y", "2019-02-28")):
            with self.subTest(anchor=anchor):
                result = analyze_premium(sample([1], anchor), AnalysisRequest(period))
                self.assertEqual(result["window"]["requested_start"], expected)
                self.assertEqual(result["window"]["requested_end"], anchor)

    def test_range_shortage_does_not_block_existing_data(self):
        result = analyze_premium(sample([1, 2, 3]), AnalysisRequest("1y", ma_periods=(1,)))
        self.assertEqual(result["window"]["rows"], 3)
        self.assertEqual(result["status"], "ok")
        self.assertIn("WINDOW_NOT_COVERED", {w["code"] for w in result["warnings"]})

    def test_empty_window_and_all_missing_do_not_generate_zeros(self):
        empty = analyze_premium(sample([1, 2]), AnalysisRequest("custom", "2019-01-01", "2019-02-01"))
        self.assertEqual(empty["status"], "empty_window")
        self.assertEqual(empty["series"], [])
        self.assertEqual(empty["current"]["premium"], "2")
        self.assertIsNone(empty["current"]["percentile_rank"])
        missing = analyze_premium(sample([None, None]))
        self.assertIsNone(missing["current"]["premium"])
        self.assertTrue(all(value is None for value in missing["quantiles"].values()))
        json.dumps(missing, allow_nan=False)

    def test_invalid_requests_fail_without_repair(self):
        requests = [AnalysisRequest("unknown"), AnalysisRequest("all", start="2020-01-01"),
                    AnalysisRequest("custom"), AnalysisRequest("custom", "2020-02-30", "2020-03-01"),
                    AnalysisRequest("custom", "2020-02-01", "2020-01-01"),
                    AnalysisRequest("custom", "20200101", "2020-01-03")]
        requests += [AnalysisRequest(ma_periods=periods) for periods in ((), (0,), (-1,), (True,), (1.5,), ("20",), (9007199254740992,))]
        for request in requests:
            with self.subTest(request=request), self.assertRaises(FundlabError) as caught:
                analyze_premium(sample([1, 2]), request)
            self.assertEqual(caught.exception.code, "VALIDATION_ERROR")

    def test_input_is_unchanged_and_removed_labels_are_absent(self):
        series = sample([1, 2, 3])
        before = deepcopy(series)
        result = analyze_premium(series, AnalysisRequest(ma_periods=(2, 2, 3)))
        self.assertEqual(series, before)
        self.assertEqual(result["average_periods"], [2, 3])
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
        for label in ("极低", "偏低", "正常", "偏高", "极高"):
            self.assertNotIn(label, encoded)


if __name__ == "__main__":
    unittest.main()
