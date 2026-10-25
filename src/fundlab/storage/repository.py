"""SQLite 事务、幂等更新、修订日志及运行记录。"""
from contextlib import closing, contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3

from fundlab.models import Fund, HistoryRecord, utc_now
from fundlab.errors import FundlabError
from fundlab.parsers.history import FIELD_DEFINITIONS


def dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


@dataclass(frozen=True)
class HistorySnapshot:
    fund: Fund
    rows: list[dict]
    coverage: dict


def read_history_snapshot(path: Path, code: str) -> HistorySnapshot:
    """同一只读事务取得基金、累计行情和来源说明；不执行建表或字段写入。"""
    if not path.is_file():
        raise FundlabError("NO_DATA", "还没有本地数据库，请先采集基金数据。")
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=15)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise FundlabError("DATABASE_VERSION", "本地数据库版本不受当前分析模块支持。")
            fund_row = db.execute("SELECT source,code,name,category,source_url FROM funds WHERE source='jisilu' AND code=?",
                                  (code,)).fetchone()
            if fund_row is None:
                raise FundlabError("NO_DATA", "本地没有该基金，请先采集。")
            records = db.execute("""SELECT h.data_json,h.fetched_at FROM history h JOIN funds f ON f.id=h.fund_id
                WHERE f.source='jisilu' AND f.code=? ORDER BY h.price_date ASC""", (code,)).fetchall()
            rows = [{**json.loads(row[0]), "fetched_at": row[1]} for row in records]
            coverage = {}
            for row in db.execute("SELECT result_json FROM update_runs WHERE code=? AND result_json IS NOT NULL ORDER BY started_at DESC", (code,)):
                result = json.loads(row[0])
                if result.get("status") in {"succeeded", "export_failed", "saved_pending_export"} and result.get("coverage"):
                    coverage = result["coverage"]
                    break
            return HistorySnapshot(Fund(**dict(fund_row)), rows, coverage)
    except (sqlite3.Error, json.JSONDecodeError, TypeError, KeyError):
        raise FundlabError("STORAGE_READ_FAILED", "未能读取本地行情快照，请检查数据库；分析未修改数据。") from None


class Repository:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise RuntimeError("数据库版本高于本程序支持版本，不能降级写入")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS funds(
                    id INTEGER PRIMARY KEY, source TEXT NOT NULL, code TEXT NOT NULL,
                    name TEXT NOT NULL, category TEXT NOT NULL, source_url TEXT NOT NULL,
                    UNIQUE(source,code));
                CREATE TABLE IF NOT EXISTS update_runs(
                    run_id TEXT PRIMARY KEY, code TEXT NOT NULL, trigger TEXT NOT NULL,
                    mode TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT,
                    status TEXT NOT NULL, result_json TEXT);
                CREATE TABLE IF NOT EXISTS history(
                    fund_id INTEGER NOT NULL REFERENCES funds(id), price_date TEXT NOT NULL,
                    data_json TEXT NOT NULL, raw_json TEXT NOT NULL, fetched_at TEXT NOT NULL,
                    last_run_id TEXT NOT NULL REFERENCES update_runs(run_id),
                    PRIMARY KEY(fund_id,price_date));
                CREATE TABLE IF NOT EXISTS record_changes(
                    id INTEGER PRIMARY KEY, fund_id INTEGER NOT NULL REFERENCES funds(id),
                    price_date TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES update_runs(run_id),
                    changed_at TEXT NOT NULL, changes_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS field_definitions(
                    key TEXT PRIMARY KEY, source_key TEXT NOT NULL, label TEXT NOT NULL, unit TEXT NOT NULL);
                PRAGMA user_version=1;
                COMMIT;
            """)
            db.execute("INSERT OR IGNORE INTO schema_migrations VALUES (1,?)", (utc_now(),))
            db.executemany("INSERT OR REPLACE INTO field_definitions VALUES (?,?,?,?)", FIELD_DEFINITIONS)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=15000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def start_run(self, run_id: str, code: str, mode: str, trigger: str = "manual"):
        with self.connection() as db:
            db.execute("INSERT INTO update_runs(run_id,code,trigger,mode,started_at,status) VALUES(?,?,?,?,?,'running')",
                       (run_id, code, trigger, mode, utc_now()))

    def finish_run(self, result: dict):
        with self.connection() as db:
            db.execute("UPDATE update_runs SET completed_at=?,status=?,result_json=? WHERE run_id=?",
                       (utc_now(), result["status"], dump(result), result["run_id"]))

    def commit_window(self, fund: Fund, records: list[HistoryRecord], run_id: str, coverage: dict | None = None) -> dict:
        if not records:
            raise ValueError("不能提交空窗口")
        counts = {"inserted": 0, "updated": 0, "unchanged": 0}
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""INSERT INTO funds(source,code,name,category,source_url) VALUES(?,?,?,?,?)
                ON CONFLICT(source,code) DO UPDATE SET name=excluded.name,category=excluded.category,source_url=excluded.source_url""",
                       (fund.source, fund.code, fund.name, fund.category, fund.source_url))
            fund_id = db.execute("SELECT id FROM funds WHERE source=? AND code=?", (fund.source, fund.code)).fetchone()[0]
            for record in records:
                data = record.data()
                previous = db.execute("SELECT data_json FROM history WHERE fund_id=? AND price_date=?",
                                      (fund_id, record.price_date)).fetchone()
                if previous is None:
                    counts["inserted"] += 1
                else:
                    old = json.loads(previous[0])
                    changes = {key: {"old": old.get(key), "new": data.get(key)}
                               for key in old.keys() | data.keys() if old.get(key) != data.get(key)}
                    if changes:
                        counts["updated"] += 1
                        db.execute("INSERT INTO record_changes(fund_id,price_date,run_id,changed_at,changes_json) VALUES(?,?,?,?,?)",
                                   (fund_id, record.price_date, run_id, utc_now(), dump(changes)))
                    else:
                        counts["unchanged"] += 1
                db.execute("""INSERT INTO history VALUES(?,?,?,?,?,?)
                    ON CONFLICT(fund_id,price_date) DO UPDATE SET data_json=excluded.data_json,
                    raw_json=excluded.raw_json,fetched_at=excluded.fetched_at,last_run_id=excluded.last_run_id""",
                           (fund_id, record.price_date, dump(data), dump(record.raw), record.fetched_at, run_id))
            # 数据和“已保存、待导出”的运行结果同事务提交，便于崩溃恢复。
            info = db.execute("SELECT count(*),min(price_date),max(price_date) FROM history WHERE fund_id=?", (fund_id,)).fetchone()
            stats = {**counts, "total": info[0], "stored_start": info[1], "stored_end": info[2]}
            pending = {"run_id": run_id, "code": fund.code, "name": fund.name,
                       "status": "saved_pending_export", "data_saved": True, **stats, "coverage": coverage or {}}
            db.execute("UPDATE update_runs SET status='saved_pending_export',result_json=? WHERE run_id=?", (dump(pending), run_id))
            return stats

    def get_fund(self, code: str) -> Fund | None:
        with self.connection() as db:
            row = db.execute("SELECT source,code,name,category,source_url FROM funds WHERE source='jisilu' AND code=?", (code,)).fetchone()
            return Fund(**dict(row)) if row else None

    def query_history(self, code: str) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("""SELECT h.data_json,h.fetched_at FROM history h JOIN funds f ON f.id=h.fund_id
                WHERE f.source='jisilu' AND f.code=? ORDER BY h.price_date DESC""", (code,)).fetchall()
            return [{**json.loads(row[0]), "fetched_at": row[1]} for row in rows]

    def list_runs(self, limit: int = 20) -> list[dict]:
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM update_runs ORDER BY started_at DESC LIMIT ?", (limit,))]

    def latest_coverage(self, code: str) -> dict:
        with self.connection() as db:
            rows = db.execute("SELECT result_json FROM update_runs WHERE code=? AND result_json IS NOT NULL ORDER BY started_at DESC", (code,))
            for row in rows:
                result = json.loads(row[0])
                if result.get("status") in {"succeeded", "export_failed", "saved_pending_export"} and result.get("coverage"):
                    return result.get("coverage", {})
        return {}
