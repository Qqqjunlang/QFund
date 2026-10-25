"""已验证 QDII 十列表格的严格解析；拒绝整批中的歧义或损坏行。"""
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
import json
import re

from fundlab.errors import FundlabError
from fundlab.models import HistoryRecord
from fundlab.validation import validate_code

# key、来源键、中文标题、单位；比例内部为小数，金额/份额保留页面的万单位。
FIELD_DEFINITIONS = (
    ("price_date", "price_dt", "价格日期", "日期"),
    ("close", "price", "收盘价", "单位价格"),
    ("nav_date", "net_value_dt", "净值日期", "日期"),
    ("nav", "net_value", "净值", "单位净值"),
    ("source_premium", "discount_rt", "溢价率", "比例"),
    ("turnover", "volume", "成交额(万元)", "万元"),
    ("shares", "amount", "盘前份额(万份)", "万份"),
    ("shares_added", "amount_incr", "盘前新增(万份)", "万份"),
    ("shares_change", "amount_increase_rt", "份额涨幅", "比例"),
    ("index_change", "ref_increase_rt", "指数涨幅", "比例"),
)
RATIO_FIELDS = {"source_premium", "shares_change", "index_change"}
MISSING = {"", "-", "--", "N/A", "null"}


def canonical_decimal(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def parse_date(value, *, required: bool = False) -> str | None:
    if value is None or str(value).strip() in MISSING:
        if required:
            raise ValueError("缺少价格日期")
        return None
    text = str(value).strip()
    if date.fromisoformat(text).isoformat() != text:
        raise ValueError("日期不是 YYYY-MM-DD")
    return text


def parse_number(value, *, ratio: bool = False, nonnegative: bool = False) -> str | None:
    if value is None or str(value).strip() in MISSING:
        return None
    if isinstance(value, bool):
        raise ValueError("布尔值不是行情数值")
    text = str(value).strip()
    if text.endswith("%"):
        if not ratio:
            raise ValueError("非比例字段出现百分号")
        text = text[:-1]
    if "," in text:
        if not re.fullmatch(r"[+-]?[0-9]{1,3}(,[0-9]{3})+(\.[0-9]+)?", text):
            raise ValueError("千分位格式错误")
        text = text.replace(",", "")
    number = Decimal(text)
    if not number.is_finite() or (nonnegative and number < 0):
        raise ValueError("非有限数值或不允许的负值")
    # 限制异常输入大小，正常源值远低于此精度。
    if len(number.as_tuple().digits) > 40 or abs(number.adjusted()) > 40:
        raise ValueError("数值超出支持精度")
    with localcontext() as ctx:
        ctx.prec = 60
        return canonical_decimal(number / 100 if ratio else number)


def parse_history(rows: list, code: str, fetched_at: str) -> list[HistoryRecord]:
    code = validate_code(code)
    if not isinstance(rows, list) or not rows:
        raise FundlabError("NO_DATA", "未取得历史记录；没有覆盖或删除本地数据。")
    records = []
    seen = set()
    for index, row in enumerate(rows, start=1):
        try:
            cell = row["cell"]
            if not isinstance(cell, dict) or str(cell["fund_id"]) != code:
                raise ValueError("基金代码不匹配")
            values = {}
            for key, source, _, _ in FIELD_DEFINITIONS:
                value = cell[source]  # 字段消失必须显式失败，不自动补成空列。
                values[key] = (parse_date(value, required=key == "price_date")
                               if key in ("price_date", "nav_date") else
                               parse_number(value, ratio=key in RATIO_FIELDS,
                                            nonnegative=key in {"close", "nav", "turnover", "shares"}))
            price_date = values["price_date"]
            if str(row["id"]) != price_date or price_date in seen:
                raise ValueError("行标识不一致或同日多条记录")
            seen.add(price_date)
            calculated = None
            if values["close"] is not None and values["nav"] is not None and Decimal(values["nav"]) > 0:
                with localcontext() as ctx:
                    ctx.prec = 40
                    calculated = canonical_decimal(Decimal(values["close"]) / Decimal(values["nav"]) - 1)
            known = {source for _, source, _, _ in FIELD_DEFINITIONS} | {"fund_id"}
            extras = {key: value for key, value in cell.items() if key not in known}
            # 确认可无损写入 JSON；不把无法解析的对象用 str() 隐藏掉。
            json.dumps(row, ensure_ascii=False, allow_nan=False)
            records.append(HistoryRecord(**values, calculated_premium=calculated,
                                         source_row_key=str(row["id"]), extra_fields=extras,
                                         raw=row, fetched_at=fetched_at))
        except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError):
            raise FundlabError("SCHEMA_CHANGED", f"第 {index} 条历史记录的字段、日期或数值异常，整批未保存。") from None
    return sorted(records, key=lambda record: record.price_date)
