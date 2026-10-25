"""独立调度记录库：先落盘再执行；读计划不建库、不修改行情或名单。"""
from contextlib import closing, contextmanager
import json
import sqlite3

from fundlab.errors import FundlabError

FIELDS = ("slot", "code", "name", "merged_from", "merged_count", "status", "phase", "attempts",
          "export_attempts", "run_id", "data_run_id", "error_code", "message", "retry_at", "updated_at", "completed_at")


class ScheduleStore:
    def __init__(self, config):
        self.path = config.schedule_path

    @contextmanager
    def connection(self, *, write=False):
        try:
            if write:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            target = str(self.path) if write else self.path.resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(target, uri=not write, timeout=15)) as db:
                db.row_factory = sqlite3.Row
                if not write:
                    db.execute("PRAGMA query_only=ON")
                with db:
                    db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                    version = db.execute("PRAGMA user_version").fetchone()[0]
                    if write and version == 0 and not db.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
                        db.execute("""CREATE TABLE jobs(
                            slot TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL,
                            merged_from TEXT NOT NULL, merged_count INTEGER NOT NULL,
                            status TEXT NOT NULL, phase TEXT NOT NULL, attempts INTEGER NOT NULL,
                            export_attempts INTEGER NOT NULL, run_id TEXT, data_run_id TEXT,
                            error_code TEXT, message TEXT NOT NULL, retry_at TEXT, updated_at TEXT NOT NULL,
                            completed_at TEXT, PRIMARY KEY(slot,code))""")
                        db.execute("""CREATE TABLE checks(id INTEGER PRIMARY KEY, checked_at TEXT NOT NULL,
                            status TEXT NOT NULL, message TEXT NOT NULL)""")
                        db.execute("PRAGMA user_version=1")
                    elif version != 1:
                        raise FundlabError("SCHEDULE_VERSION", "自动执行记录版本不受支持，原文件已保留。")
                    yield db
        except (sqlite3.Error, OSError):
            raise FundlabError("SCHEDULE_WRITE_FAILED" if write else "SCHEDULE_READ_FAILED",
                               "自动执行记录无法保存，请检查磁盘；若刚执行过，请先核对数据，不要重复采集。" if write else
                               "自动执行记录无法读取，请保留原文件并检查。") from None

    def read(self):
        if not self.path.exists():
            return {"latest": {}, "recent": [], "check": None}
        with self.connection() as db:
            # 每只基金只取最新时段；历史行保留，限制只影响页面返回数量。
            rows = [dict(row) for row in db.execute("SELECT j.* FROM jobs j JOIN (SELECT code,max(slot) AS slot FROM jobs GROUP BY code) m USING(code,slot)")]
            recent = [dict(row) for row in db.execute("SELECT * FROM jobs ORDER BY updated_at DESC,code LIMIT 50")]
            check = db.execute("SELECT checked_at,status,message FROM checks ORDER BY id DESC LIMIT 1").fetchone()
            return {"latest": {row["code"]: row for row in rows}, "recent": recent,
                    "check": dict(check) if check else None}

    def plan(self, jobs):
        with self.connection(write=True) as db:
            for job in jobs:
                db.execute("UPDATE jobs SET status='superseded',message='已合并至更新的到期时段。' WHERE code=? AND slot<? AND status NOT IN ('succeeded','skipped','superseded')",
                           (job["code"], job["slot"]))
                db.execute(f"INSERT OR IGNORE INTO jobs({','.join(FIELDS)}) VALUES({','.join('?' for _ in FIELDS)})",
                           tuple(job[key] for key in FIELDS))

    def save(self, job):
        with self.connection(write=True) as db:
            changed = db.execute(f"UPDATE jobs SET {','.join(key+'=?' for key in FIELDS[2:])} WHERE slot=? AND code=?",
                                 tuple(job[key] for key in FIELDS[2:]) + (job["slot"], job["code"]))
            if changed.rowcount != 1:
                raise FundlabError("SCHEDULE_RECORD_MISSING", "到期执行记录缺失，本次停止，请先核对原记录。")

    def checked(self, now, status, message):
        with self.connection(write=True) as db:
            db.execute("INSERT INTO checks(checked_at,status,message) VALUES(?,?,?)", (now, status, message))
            db.execute("DELETE FROM checks WHERE id NOT IN (SELECT id FROM checks ORDER BY id DESC LIMIT 50)")


def market_run(config, run_id):
    """崩溃恢复只读核对原运行。不能以调度日志缺少结果推断行情没保存。"""
    if not config.database_path.is_file():
        raise FundlabError("NO_DATA", "本地行情库缺失，请先恢复数据；自动执行已停止。")
    try:
        with closing(sqlite3.connect(config.database_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=15)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise FundlabError("DATABASE_VERSION", "行情库版本不受支持，自动执行恢复已停止。")
            row = db.execute("SELECT * FROM update_runs WHERE run_id=?", (run_id,)).fetchone()
            if not row:
                return None
            result = json.loads(row["result_json"] or "{}")
            if not isinstance(result, dict):
                raise ValueError()
            saved = bool(result.get("data_saved")) or row["status"] in {"succeeded", "saved_pending_export", "export_failed"}
            saved = saved or bool(db.execute("SELECT 1 FROM history WHERE last_run_id=? LIMIT 1", (run_id,)).fetchone())
            return {"status": row["status"], "error_code": result.get("error_code"), "data_saved": saved,
                    "message": result.get("message", ""), "mode": row["mode"], "code": row["code"]}
    except (sqlite3.Error, ValueError, TypeError):
        raise FundlabError("STORAGE_READ_FAILED", "不能确认上次自动更新是否已保存，已停止重试，请先核对行情库。") from None
