"""本地工作台的只读概况、日期查询与下载服务。"""
from contextlib import closing
from dataclasses import asdict
from datetime import date
from decimal import Decimal, localcontext
import json
import sqlite3

from fundlab.config import AppConfig
from fundlab.errors import FundlabError
from fundlab.exports.service import render_history_file
from fundlab.models import utc_now
from fundlab.parsers.history import FIELD_DEFINITIONS, RATIO_FIELDS, canonical_decimal
from fundlab.storage.repository import read_history_snapshot
from fundlab.storage.migrations import READ_VERSIONS
from fundlab.storage.effective import effective_rows, premium_value
from fundlab.validation import validate_code


def overview(config: AppConfig) -> dict:
    result = {"generated_at": utc_now(), "funds": [], "latest": None}
    if not config.database_path.is_file():
        return result
    try:
        with closing(sqlite3.connect(config.database_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=15)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in READ_VERSIONS:
                raise FundlabError("DATABASE_VERSION", "本地数据库版本暂不支持。")
            result["funds"] = [dict(row) for row in db.execute("""
                SELECT f.code,f.name,f.category,count(h.price_date) AS records,
                       min(h.price_date) AS start,max(h.price_date) AS end,max(h.fetched_at) AS fetched_at
                FROM funds f LEFT JOIN history h ON h.fund_id=f.id
                WHERE f.source='jisilu' GROUP BY f.id ORDER BY f.code
            """)]
            if version >= 3:
                result["funds"] = [f for f in result["funds"] if not db.execute(
                    "SELECT 1 FROM fund_state s JOIN funds f ON f.id=s.fund_id WHERE f.code=? AND s.state!='active'", (f["code"],)).fetchone()]
            if version >= 2:
                for fund in result["funds"]:
                    fund_id = db.execute("SELECT id FROM funds WHERE source='jisilu' AND code=?", (fund["code"],)).fetchone()[0]
                    rows = effective_rows(db, fund_id, version)
                    fund.update(records=len(rows), start=rows[0]["price_date"] if rows else None,
                                end=rows[-1]["price_date"] if rows else None,
                                fetched_at=max((r["fetched_at"] for r in rows), default=None))
            row = db.execute("""SELECT code,status,started_at,completed_at,result_json
                FROM update_runs WHERE mode NOT IN ('export','local') ORDER BY started_at DESC LIMIT 1""").fetchone()
            if row:
                latest = dict(row)
                raw = json.loads(latest.pop("result_json") or "{}")
                latest["result"] = {key: raw[key] for key in ("name", "inserted", "updated", "unchanged") if key in raw}
                result["latest"] = latest
            return result
    except (sqlite3.Error, ValueError, TypeError):
        raise FundlabError("STORAGE_READ_FAILED", "未能读取本地基金列表，请检查数据库后重试。") from None


def _selection(config: AppConfig, code: str, start: str, end: str):
    code = validate_code(code)
    try:
        for value in (start, end):
            if value and date.fromisoformat(value).isoformat() != value:
                raise ValueError()
        if start and end and start > end:
            raise ValueError()
    except ValueError:
        raise FundlabError("VALIDATION_ERROR", "日期须为有效的 YYYY-MM-DD，开始日期不能晚于结束日期。") from None
    snapshot = read_history_snapshot(config.database_path, code)
    rows = sorted(snapshot.rows, key=lambda r: r["price_date"], reverse=True)
    chosen = [r for r in rows if (not start or r["price_date"] >= start) and (not end or r["price_date"] <= end)]
    return snapshot, rows, chosen


def history_page(config: AppConfig, code: str, start: str = "", end: str = "", page: int = 1) -> dict:
    if not isinstance(page, int) or not 1 <= page <= 1_000_000:
        raise FundlabError("VALIDATION_ERROR", "页码须为有效正整数。")
    snapshot, all_rows, rows = _selection(config, code, start, end)
    page_size = 50
    pages = max(1, (len(rows) + page_size - 1) // page_size)
    page = min(page, pages)
    display = []
    row_meta = []
    for row in rows[(page - 1) * page_size:page * page_size]:
        values = {}
        for key, _, _, _ in FIELD_DEFINITIONS:
            value = premium_value(row) if key == "source_premium" else row[key]
            if value is not None and key in RATIO_FIELDS:
                with localcontext() as ctx:
                    ctx.prec = 60
                    value = canonical_decimal(Decimal(value) * 100) + "%"
            values[key] = value
        display.append(values)
        row_meta.append({key: row[key] for key in ("revision", "record_origin", "protected_fields", "field_origins", "premium_origin")})
    return {"fund": asdict(snapshot.fund), "columns": [{"key": k, "label": label} for k, _, label, _ in FIELD_DEFINITIONS],
            "rows": display, "row_meta": row_meta, "total": len(all_rows), "matched": len(rows), "page": page, "pages": pages,
            "page_size": page_size, "start": start, "end": end,
            "local_start": all_rows[-1]["price_date"] if all_rows else None,
            "local_end": all_rows[0]["price_date"] if all_rows else None,
            "fetched_at": max((r["fetched_at"] for r in all_rows), default=None)}


def download_history(config: AppConfig, code: str, format: str, start: str = "", end: str = "") -> tuple[str, bytes]:
    snapshot, _, rows = _selection(config, code, start, end)
    content = render_history_file(snapshot.fund, rows, snapshot.coverage, format)
    return f"{snapshot.fund.code}_history_{rows[-1]['price_date']}_{rows[0]['price_date']}.{format}", content
