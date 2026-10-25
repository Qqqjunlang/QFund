"""应用数据类型：十进制数以可还原精度的字符串传递和保存。"""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Fund:
    code: str
    name: str
    category: str
    source_url: str
    source: str = "jisilu"


@dataclass(frozen=True)
class HistoryRecord:
    price_date: str
    close: str | None
    nav_date: str | None
    nav: str | None
    source_premium: str | None
    turnover: str | None
    shares: str | None
    shares_added: str | None
    shares_change: str | None
    index_change: str | None
    calculated_premium: str | None
    source_row_key: str
    extra_fields: dict[str, Any]
    raw: dict[str, Any]
    fetched_at: str

    def data(self) -> dict:
        """用于版本比较；采集时间和原始值格式变化不构成数值修订。"""
        result = asdict(self)
        for key in ("raw", "fetched_at"):
            result.pop(key)
        return result


@dataclass(frozen=True)
class SourceWindow:
    fund: Fund
    rows: list[dict]
    fetched_at: str
    coverage: dict


@dataclass
class UpdateResult:
    run_id: str
    code: str
    name: str = ""
    status: str = "running"
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    total: int = 0
    coverage: dict = field(default_factory=dict)
    exports: list[dict] = field(default_factory=list)
    error_code: str | None = None
    message: str = ""
    data_saved: bool = False

    def to_dict(self) -> dict:
        return asdict(self)
