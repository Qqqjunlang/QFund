"""行情库版本升级；只由显式写入口调用，旧版一致性备份永久保留。"""
from contextlib import closing
from pathlib import Path
import sqlite3
from uuid import uuid4

from fundlab.models import utc_now

SCHEMA_VERSION = 3
READ_VERSIONS = (1, 2, 3)

TABLES = (
    """CREATE TABLE field_overrides(
        fund_id INTEGER NOT NULL REFERENCES funds(id), price_date TEXT NOT NULL,
        field TEXT NOT NULL, value_json TEXT NOT NULL, changed_at TEXT NOT NULL,
        PRIMARY KEY(fund_id,price_date,field),
        FOREIGN KEY(fund_id,price_date) REFERENCES history(fund_id,price_date))""",
    """CREATE TABLE deleted_dates(
        fund_id INTEGER NOT NULL REFERENCES funds(id), price_date TEXT NOT NULL,
        deleted_at TEXT NOT NULL, PRIMARY KEY(fund_id,price_date))""",
    """CREATE TABLE data_revisions(
        fund_id INTEGER PRIMARY KEY REFERENCES funds(id), revision INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE mutation_events(
        id INTEGER PRIMARY KEY, fund_id INTEGER NOT NULL REFERENCES funds(id),
        price_date TEXT NOT NULL, kind TEXT NOT NULL, before_json TEXT NOT NULL,
        after_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
)

MAINTENANCE_TABLES = (
    """CREATE TABLE fund_state(fund_id INTEGER PRIMARY KEY REFERENCES funds(id),
        state TEXT NOT NULL CHECK(state IN ('active','trashing','trashed','restoring')),
        changed_at TEXT NOT NULL)""",
    """CREATE TABLE maintenance_operations(operation_id TEXT PRIMARY KEY,
        fund_id INTEGER NOT NULL REFERENCES funds(id), kind TEXT NOT NULL CHECK(kind IN ('trash','restore')),
        phase TEXT NOT NULL CHECK(phase IN ('pending','done')), created_at TEXT NOT NULL)""",
    """CREATE TABLE fund_groups(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
        revision INTEGER NOT NULL DEFAULT 1)""",
    """CREATE TABLE fund_group_members(group_id INTEGER NOT NULL REFERENCES fund_groups(id) ON DELETE CASCADE,
        fund_id INTEGER NOT NULL REFERENCES funds(id), PRIMARY KEY(group_id,fund_id))""",
)


def migrate(db: sqlite3.Connection, path: Path, *, backup_existing=True):
    """在调用方事务内升级；BEGIN IMMEDIATE 阻止备份期间并发写入。"""
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version not in (1, 2):
        raise RuntimeError("仅支持行情库版本1或2升级到版本3")
    backup = path.with_name(path.name + f'.schema{version}-' + uuid4().hex + '.bak')
    # 不能在持写事务的同一个连接上调用backup；另一个只读连接包含已提交WAL。
    if backup_existing:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(backup)) as destination:
                source.backup(destination)
                if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('迁移备份完整性校验失败，未升级')
    if version == 1:
        for statement in TABLES:
            db.execute(statement)
        db.execute('INSERT INTO schema_migrations VALUES(2,?)', (utc_now(),))
    for statement in MAINTENANCE_TABLES:
        db.execute(statement)
    if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('行情库外键检查失败，未升级')
    db.execute('INSERT INTO schema_migrations VALUES(3,?)', (utc_now(),))
    db.execute('PRAGMA user_version=3')
