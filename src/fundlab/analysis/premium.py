"""溢价率时序：复用入库字段，仅作计算需要的排序和单位转换。"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext

from fundlab.errors import FundlabError


@dataclass(frozen=True)
class PremiumPoint:
    price_date: str
    premium: str | None  # 百分点数值，例如 1.25 表示 1.25%。


@dataclass(frozen=True)
class PremiumSeries:
    points: tuple[PremiumPoint, ...]

    @property
    def latest_valid(self) -> PremiumPoint | None:
        return next((point for point in reversed(self.points) if point.premium is not None), None)


def premium_series(rows: list[dict]) -> PremiumSeries:
    """不改写行情，不补值；source_premium 为第二阶段已解析的比例小数。"""
    if not rows:
        raise FundlabError("NO_DATA", "本地没有行情，请先采集该基金。")
    points = []
    try:
        for row in sorted(rows, key=lambda item: item["price_date"]):
            value = row["source_premium"]
            if value is None:
                converted = None
            else:
                decimal = Decimal(value)
                if not decimal.is_finite():
                    raise ValueError("nonfinite stored premium")
                with localcontext() as context:
                    context.prec = max(28, len(decimal.as_tuple().digits) + 4)
                    converted = format(decimal * 100, "f")
            points.append(PremiumPoint(row["price_date"], converted))
    except (KeyError, InvalidOperation, TypeError, ValueError):
        raise FundlabError("DATA_CONTRACT_ERROR", "本地数据不符合已入库字段约定，分析已停止；未修改行情。") from None
    return PremiumSeries(tuple(points))
