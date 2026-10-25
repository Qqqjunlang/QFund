"""有来源和覆盖范围的沪深交易日历；未知日期明确失败，不回退为工作日。"""
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
import json
from pathlib import Path
from urllib.parse import urlsplit

from fundlab.errors import FundlabError


@dataclass(frozen=True)
class TradingCalendar:
    start: date
    end: date
    closed: frozenset
    verified_on: str
    sources: tuple

    def require(self, day):
        if not self.start <= day <= self.end:
            raise FundlabError("CALENDAR_UNAVAILABLE", f"交易日历未覆盖{day.isoformat()}，请先更新经核对的沪深交易日历；到期采集已暂停。")

    def is_open(self, day):
        self.require(day)
        return day.weekday() < 5 and day not in self.closed

    def public(self):
        return {"market": "SSE_SZSE", "coverage_start": self.start.isoformat(), "coverage_end": self.end.isoformat(),
                "verified_on": self.verified_on, "sources": list(self.sources)}


@lru_cache(maxsize=1)
def load_calendar():
    try:
        data = json.loads(Path(__file__).with_name("calendar-2026.json").read_text(encoding="utf-8"))
        if data["schema"] != 1 or data["market"] != "SSE_SZSE":
            raise ValueError()
        start, end = date.fromisoformat(data["coverage_start"]), date.fromisoformat(data["coverage_end"])
        if start > end or date.fromisoformat(data["verified_on"]) < start:
            raise ValueError()
        sources = tuple(data["sources"])
        if len(sources) < 2 or any(urlsplit(url).scheme != "https" or urlsplit(url).hostname not in {
                "www.sse.com.cn", "www.szse.cn", "investor.szse.cn"} for url in sources):
            raise ValueError()
        closed = set()
        if not data["closed_ranges"]:
            raise ValueError()
        for begin, finish in data["closed_ranges"]:
            begin, finish = date.fromisoformat(begin), date.fromisoformat(finish)
            if not start <= begin <= finish <= end:
                raise ValueError()
            while begin <= finish:
                closed.add(begin)
                begin += timedelta(days=1)
        return TradingCalendar(start, end, frozenset(closed), data["verified_on"], sources)
    except (OSError, ValueError, TypeError, KeyError):
        raise FundlabError("CALENDAR_INVALID", "交易日历无法确认，请保留原文件并检查；到期采集已暂停，手动获取仍可使用。") from None
