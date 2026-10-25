"""自动更新名单管理；本步不注册任务、不触发采集，也不修改行情库。"""
from contextlib import closing, contextmanager
import sqlite3

from fundlab.config import AppConfig
from fundlab.errors import FundlabError
from fundlab.models import utc_now
from fundlab.services.data_query import overview
from fundlab.scheduling.store import ScheduleStore
from fundlab.validation import validate_code


class WatchlistService:
    def __init__(self, config: AppConfig):
        self.config = config

    @contextmanager
    def _connection(self, *, write=False):
        path = self.config.watchlist_path
        try:
            if write:
                path.parent.mkdir(parents=True, exist_ok=True)
            target = str(path) if write else path.resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(target, uri=not write, timeout=15)) as db:
                db.row_factory = sqlite3.Row
                if not write:
                    db.execute("PRAGMA query_only=ON")
                with db:
                    db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                    version = db.execute("PRAGMA user_version").fetchone()[0]
                    if version == 0 and write and not db.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
                        db.execute("""CREATE TABLE watchlist(
                            code TEXT PRIMARY KEY CHECK(length(code)=6 AND code NOT GLOB '*[^0-9]*'),
                            enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                            added_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
                        db.execute("PRAGMA user_version=1")
                    elif version != 1:
                        raise FundlabError("WATCHLIST_VERSION", "自动更新名单版本不受支持，原文件已保留。")
                    yield db
        except (sqlite3.Error, OSError):
            raise FundlabError("WATCHLIST_WRITE_FAILED" if write else "WATCHLIST_READ_FAILED",
                               "未能保存自动更新名单，请刷新核对后重试。" if write else
                               "未能读取自动更新名单，请检查名单文件后重试。") from None

    @staticmethod
    def _code(value):
        if not isinstance(value, str):
            raise FundlabError("VALIDATION_ERROR", "请输入六位基金代码，并保留开头的零。")
        return validate_code(value)

    def _available(self):
        return [fund for fund in overview(self.config)["funds"] if fund["records"] > 0]

    def state(self):
        available = self._available()
        funds = {fund["code"]: fund for fund in available}
        rows = []
        if self.config.watchlist_path.exists():
            with self._connection() as db:
                rows = [dict(row) for row in db.execute("SELECT * FROM watchlist ORDER BY code")]
        items = []
        latest = ScheduleStore(self.config).read()["latest"]
        for row in rows:
            fund = funds.get(row["code"])
            items.append({**row, "enabled": bool(row["enabled"]),
                          "name": fund["name"] if fund else "本地数据不可用",
                          "available": fund is not None, "last_auto_result": latest.get(row["code"])})
        return {"items": items, "available_funds": available,
                "enabled_count": sum(item["enabled"] for item in items)}

    def change(self, action, code, enabled=None):
        code = self._code(code)
        if action not in {"add", "set_enabled", "remove"}:
            raise FundlabError("VALIDATION_ERROR", "自动更新名单操作不正确。")
        if action == "set_enabled" and type(enabled) is not bool:
            raise FundlabError("VALIDATION_ERROR", "名单启用状态须为是或否。")
        if action == "add" or (action == "set_enabled" and enabled):
            if not any(fund["code"] == code for fund in self._available()):
                raise FundlabError("VALIDATION_ERROR", "请先获取这只基金的本地数据，再加入或启用自动更新名单。")
        with self._connection(write=True) as db:
            now = utc_now()
            if action == "add":
                # 重复加入不重置加入时间，也不把已停用的项目重新启用。
                db.execute("INSERT OR IGNORE INTO watchlist VALUES(?,1,?,?)", (code, now, now))
            elif action == "remove":
                # 仅移出名单；不删除行情、归档或运行记录。重复请求仍安全。
                db.execute("DELETE FROM watchlist WHERE code=?", (code,))
            else:
                if not db.execute("SELECT 1 FROM watchlist WHERE code=?", (code,)).fetchone():
                    raise FundlabError("VALIDATION_ERROR", "名单中没有这只基金，请刷新后重试。")
                db.execute("UPDATE watchlist SET enabled=?,updated_at=? WHERE code=? AND enabled!=?",
                           (int(enabled), now, code, int(enabled)))
        return self.state()
