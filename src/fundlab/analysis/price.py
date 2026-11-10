"""收盘价走势与有效样本移动平均。"""

from collections import deque
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
import re

from fundlab.analysis.calculations import linear_percentiles
from fundlab.errors import FundlabError
from fundlab.parsers.history import canonical_decimal, parse_date, parse_number

DEFAULT_PERIODS = (5,)
REFERENCE_PERCENTILES = (30, 50, 70)
MAX_PERIOD = 10_000
MAX_PERIODS = 5


@dataclass(frozen=True)
class PricePoint:
    day: str
    close: Decimal | None
    averages: tuple[Decimal | None, ...]


def price_percentiles(points):
    valid = sorted(point.close for point in points if point.close is not None)
    with localcontext() as context:
        context.prec = 120
        return linear_percentiles(valid, REFERENCE_PERCENTILES)


def summarize_prices(rows, *, start='', end='', comparison_price=None):
    """最新值取全部历史，分位以所选日期内小于等于目标价格的有效样本占比计算。"""
    rows = sorted(rows, key=lambda row: row['price_date'])
    points = closing_prices(rows, (), start=start, end=end)
    latest = next((row for row in reversed(rows) if row['close'] is not None), None)
    target = Decimal(latest['close']) if latest else None
    if comparison_price is not None:
        try:
            if not isinstance(comparison_price, str):
                raise ValueError()
            value = parse_number(comparison_price, nonnegative=True)
            if value is None:
                raise ValueError()
            target = Decimal(value)
        except (ValueError, InvalidOperation):
            raise FundlabError('VALIDATION_ERROR', '请输入有效的非负价格，例如 1.995。') from None
    valid = [point.close for point in points if point.close is not None]
    rank = None
    if target is not None and valid:
        with localcontext() as context:
            context.prec = 60
            rank = canonical_decimal(Decimal(sum(value <= target for value in valid)) / len(valid) * 100)
    return {
        'latest_price': latest['close'] if latest else None,
        'latest_date': latest['price_date'] if latest else None,
        'records': len(rows), 'stored_start': rows[0]['price_date'], 'stored_end': rows[-1]['price_date'],
        'start': start or points[0].day, 'end': end or points[-1].day, 'valid_samples': len(valid),
        'comparison': {'mode': 'current' if comparison_price is None else 'custom',
                       'price': canonical_decimal(target) if target is not None else None,
                       'percentile_rank': rank},
    }


def parse_periods(text: str) -> tuple[int, ...]:
    if not text.strip():
        return DEFAULT_PERIODS
    parts = [part.strip() for part in text.replace('，', ',').split(',')]
    if len(parts) <= MAX_PERIODS and all(re.fullmatch(r'[0-9]{1,5}', part) for part in parts):
        periods = tuple(dict.fromkeys(map(int, parts)))
        if all(1 <= period <= MAX_PERIOD for period in periods):
            return periods
    raise FundlabError('VALIDATION_ERROR', '均线最多输入 5 个周期，各为 1 至 10000 的整数，用逗号分隔。')


def closing_prices(rows, periods, *, start='', end='') -> list[PricePoint]:
    try:
        if start:
            parse_date(start, required=True)
        if end:
            parse_date(end, required=True)
        if start and end and start > end:
            raise ValueError()
    except ValueError:
        raise FundlabError('VALIDATION_ERROR', '请核对日期，开始日期应早于或等于结束日期。') from None

    windows = [deque() for _ in periods]
    sums = [Decimal(0) for _ in periods]
    points = []
    with localcontext() as context:
        context.prec = 120
        for row in sorted(rows, key=lambda row: row['price_date']):
            day = row['price_date']
            if end and day > end:
                break
            value = Decimal(row['close']) if row['close'] is not None else None
            averages = []
            for index, (period, window) in enumerate(zip(periods, windows)):
                if value is None:
                    averages.append(None)
                    continue
                if len(window) == period:
                    sums[index] -= window.popleft()
                window.append(value)
                sums[index] += value
                averages.append(sums[index] / period if len(window) == period else None)
            if not start or day >= start:
                points.append(PricePoint(day, value, tuple(averages)))
    if not points:
        raise FundlabError('NO_DATA', '所选区间没有本地行情，请调整日期。')
    return points
