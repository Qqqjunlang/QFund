"""基金溢价估值的确定性计算；不依赖图表、数据库或采集器。"""
from calendar import monthrange
from collections import deque
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
import math
import re

from fundlab.analysis.premium import PremiumSeries
from fundlab.errors import FundlabError

QUANTILES = (10, 30, 50, 70, 90)
PERIODS = {"1m": 1, "3m": 3, "6m": 6, "1y": 12}


@dataclass(frozen=True)
class AnalysisRequest:
    period: str = "all"
    start: str | None = None
    end: str | None = None
    ma_periods: tuple[int, ...] = (20, 60)
    comparison_premium: str | None = None  # 百分点输入：12.5 表示 12.5%。


def parse_comparison_premium(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        if not isinstance(value, str) or not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", value.strip()):
            raise ValueError("invalid decimal input")
        parsed = Decimal(value.strip())
        plotted = float(parsed)
        if not math.isfinite(plotted) or (parsed != 0 and plotted == 0):
            raise ValueError("outside browser numeric range")
        return Decimal(0) if parsed == 0 else parsed
    except (ValueError, TypeError, InvalidOperation, OverflowError):
        raise FundlabError("VALIDATION_ERROR", "请输入有效溢价率数值，例如 12.5 表示 12.5%，可输入零或负数。") from None


def number(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def request_bounds(series: PremiumSeries, request: AnalysisRequest) -> tuple[str, str, tuple[int, ...]]:
    periods = request.ma_periods
    if not periods or any(type(n) is not int or not 1 <= n <= 9007199254740991 for n in periods):
        raise FundlabError("VALIDATION_ERROR", "N 日周期必须是正整数，且不能超过 9007199254740991。")
    periods = tuple(dict.fromkeys(periods))
    if request.period == "custom":
        try:
            start, end = date.fromisoformat(request.start), date.fromisoformat(request.end)
            if start.isoformat() != request.start or end.isoformat() != request.end or start > end:
                raise ValueError("invalid interval")
        except (TypeError, ValueError):
            raise FundlabError("VALIDATION_ERROR", "自定义区间须填写 YYYY-MM-DD 起止日期，且开始不晚于结束。") from None
        return start.isoformat(), end.isoformat(), periods
    if request.start is not None or request.end is not None:
        raise FundlabError("VALIDATION_ERROR", "起止日期请与 custom 区间一起使用。")
    latest = series.points[-1].price_date
    if request.period == "all":
        return series.points[0].price_date, latest, periods
    if request.period not in PERIODS:
        raise FundlabError("VALIDATION_ERROR", "区间须为 1m、3m、6m、1y、all 或 custom。")
    end = date.fromisoformat(latest)
    year, month0 = divmod(end.year * 12 + end.month - 1 - PERIODS[request.period], 12)
    try:
        start = date(year, month0 + 1, min(end.day, monthrange(year, month0 + 1)[1]))
    except ValueError:
        raise FundlabError("VALIDATION_ERROR", "所选区间超出可支持的日期范围。") from None
    return start.isoformat(), latest, periods


def analyze_premium(series: PremiumSeries, request: AnalysisRequest = AnalysisRequest()) -> dict:
    start, end, periods = request_bounds(series, request)
    comparison_premium = parse_comparison_premium(request.comparison_premium)
    points = series.points
    valid = [Decimal(p.premium) for p in points if p.premium is not None]
    precision = max([60] + [len(v.as_tuple().digits) + len(str(len(points))) + 12 for v in valid])
    with localcontext() as context:
        context.prec = precision
        queues = {n: deque() for n in periods}
        totals = {n: Decimal(0) for n in periods}
        all_rows = []
        for point in points:
            averages = {str(n): None for n in periods}
            if point.premium is not None:
                value = Decimal(point.premium)
                for n in periods:
                    queues[n].append(value)
                    totals[n] += value
                    if len(queues[n]) > n:
                        totals[n] -= queues[n].popleft()
                    if len(queues[n]) == n:
                        averages[str(n)] = number(totals[n] / n)
            all_rows.append({"price_date": point.price_date, "premium": point.premium,
                             "average_premiums": averages})
        selected = [row for row in all_rows if start <= row["price_date"] <= end]
        window_valid = sorted(Decimal(row["premium"]) for row in selected if row["premium"] is not None)
        latest = next((row for row in reversed(all_rows) if row["premium"] is not None), None)
        quantiles = {str(q): None for q in QUANTILES}
        if window_valid:
            for q in QUANTILES:
                position = Decimal(len(window_valid) - 1) * q / 100
                lower = int(position)
                upper = min(lower + 1, len(window_valid) - 1)
                quantiles[str(q)] = number(window_valid[lower] + (window_valid[upper] - window_valid[lower]) * (position - lower))
        rank = None
        if latest is not None and window_valid:
            rank = number(Decimal(sum(v <= Decimal(latest["premium"]) for v in window_valid)) / len(window_valid) * 100)
        target = comparison_premium if comparison_premium is not None else Decimal(latest["premium"]) if latest else None
        comparison_rank = None
        if target is not None and window_valid:
            comparison_rank = number(Decimal(sum(v <= target for v in window_valid)) / len(window_valid) * 100)
    first, last = points[0].price_date, points[-1].price_date
    valid_selected = [row for row in selected if row["premium"] is not None]
    covered = start >= first and end <= last
    warnings = []

    def warn(code, message, **details):
        warnings.append({"code": code, "message": message, **details})

    if not covered:
        warn("WINDOW_NOT_COVERED", f"请求区间超出本地已保存范围（{first} 至 {last}），以下结果仅基于已有数据。")
    if not window_valid:
        warn("NO_VALID_WINDOW", "所选区间没有有效溢价率，分位排名和参考值不可计算。")
    if len(selected) != len(window_valid):
        warn("MISSING_PREMIUM", "区间中的缺失溢价不参与统计，未作补值。", count=len(selected) - len(window_valid))
    if latest is None:
        warn("NO_VALID_CURRENT", "本地行情中没有有效溢价率。")
    elif latest["price_date"] != last:
        warn("CURRENT_DATE_DIFFERS", f"最新行情日期为 {last}，最新有效溢价对应 {latest['price_date']}。")
    for n in periods:
        if len(valid) < n:
            warn("AVERAGE_INSUFFICIENT", f"本地仅有 {len(valid)} 条有效溢价记录，无法计算完整的 {n} 日平均溢价率。", period=n, available=len(valid))
        unready = sum(row["premium"] is not None and row["average_premiums"][str(n)] is None for row in selected)
        if unready:
            warn("AVERAGE_SERIES_INCOMPLETE", f"所选区间中 {unready} 个日期此前不足 {n} 条有效记录，相关平均溢价率留空。", period=n, count=unready)
    return {
        "schema_version": 1, "analysis": "fund_premium",
        "status": "ok" if window_valid else "empty_window",
        "units": {"premium": "percentage_points", "percentile_rank": "percent"},
        "methods": {"percentile_rank": "count(window_premium <= latest_valid_premium) / valid_window_count * 100",
                    "quantiles": "linear interpolation at (n - 1) * p", "average_premium": "last N valid observations through each date; no future data",
                    "arithmetic": f"Decimal, precision {precision}; plot display rounded separately"},
        "window": {"preset": request.period, "requested_start": start, "requested_end": end, "anchor_date": last,
                   "stored_start": first, "stored_end": last, "stored_rows": len(points),
                   "actual_start": selected[0]["price_date"] if selected else None,
                   "actual_end": selected[-1]["price_date"] if selected else None,
                   "valid_start": valid_selected[0]["price_date"] if valid_selected else None,
                   "valid_end": valid_selected[-1]["price_date"] if valid_selected else None,
                   "rows": len(selected), "valid_samples": len(window_valid), "missing_samples": len(selected) - len(window_valid),
                   "requested_bounds_within_stored_span": covered, "trading_calendar_verified": False, "history_scope": "local_accumulated"},
        "current": {"price_date": latest["price_date"] if latest else None, "premium": latest["premium"] if latest else None,
                    "percentile_rank": rank, "average_premiums": latest["average_premiums"] if latest else {str(n): None for n in periods},
                    "valid_history_samples": len(valid)},
        "comparison": {"mode": "custom" if comparison_premium is not None else "current",
                       "premium": number(comparison_premium) if comparison_premium is not None else latest["premium"] if latest else None,
                       "price_date": None if comparison_premium is not None else latest["price_date"] if latest else None,
                       "percentile_rank": comparison_rank},
        "quantiles": quantiles, "average_periods": list(periods), "series": selected, "warnings": warnings,
    }
