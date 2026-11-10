"""本地维护存储：跨库操作阶段、分组及单日新增；不持有项目锁。"""
from contextlib import closing, contextmanager
from uuid import uuid4
import json
import sqlite3

from fundlab.errors import FundlabError
from fundlab.models import utc_now
from fundlab.storage.effective import require_active
from fundlab.storage.migrations import READ_VERSIONS
from fundlab.storage.repository import dump


@contextmanager
def read_connection(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=15)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        if db.execute('PRAGMA user_version').fetchone()[0] not in READ_VERSIONS:
            raise FundlabError('DATABASE_VERSION', '行情库版本不受支持，未修改数据。')
        yield db


def fund_id(db, code):
    row = db.execute("SELECT id FROM funds WHERE source='jisilu' AND code=?", (code,)).fetchone()
    if row is None:
        raise FundlabError('NO_DATA', '本地没有该基金。')
    return row[0]


def bump(db, identity):
    db.execute('INSERT INTO data_revisions VALUES(?,1) ON CONFLICT(fund_id) DO UPDATE SET revision=revision+1', (identity,))


def check_revision(db, identity, expected):
    row = db.execute('SELECT revision FROM data_revisions WHERE fund_id=?', (identity,)).fetchone()
    revision = row[0] if row else 0
    if type(expected) is not int or expected != revision:
        raise FundlabError('DATA_STALE', '数据已变化，请刷新后重新操作。')


def event(db, identity, day, kind, before, after):
    db.execute('INSERT INTO mutation_events(fund_id,price_date,kind,before_json,after_json,created_at) VALUES(?,?,?,?,?,?)',
               (identity, day, kind, dump(before), dump(after), utc_now()))


def maintenance_state(path):
    result = {'trash': [], 'groups': [], 'pending': []}
    if not path.is_file():
        return result
    with read_connection(path) as db:
        if db.execute('PRAGMA user_version').fetchone()[0] < 3:
            return result
        result['trash'] = [dict(row) for row in db.execute("""SELECT f.code,f.name,s.state,s.changed_at,
            coalesce(r.revision,0) AS revision FROM fund_state s JOIN funds f ON f.id=s.fund_id
            LEFT JOIN data_revisions r ON r.fund_id=f.id WHERE s.state!='active' ORDER BY f.code""")]
        result['pending'] = [dict(row) for row in db.execute("SELECT * FROM maintenance_operations WHERE phase='pending' ORDER BY created_at,operation_id")]
        for row in db.execute('SELECT * FROM fund_groups ORDER BY id'):
            codes = [r[0] for r in db.execute("""SELECT f.code FROM fund_group_members m JOIN funds f ON f.id=m.fund_id
                LEFT JOIN fund_state s ON s.fund_id=f.id WHERE m.group_id=? AND coalesce(s.state,'active')='active' ORDER BY f.code""", (row['id'],))]
            result['groups'].append({**dict(row), 'codes': codes})
    return result


def record_state(path, code):
    if not path.is_file():
        raise FundlabError('NO_DATA', '本地没有该基金。')
    with read_connection(path) as db:
        identity = fund_id(db, code)
        version = db.execute('PRAGMA user_version').fetchone()[0]
        result = {'code': code, 'revision': 0, 'protected': [], 'deleted_dates': [], 'state': 'active'}
        if version >= 2:
            row = db.execute('SELECT revision FROM data_revisions WHERE fund_id=?', (identity,)).fetchone()
            result.update(revision=row[0] if row else 0,
                protected=[dict(r) for r in db.execute('SELECT price_date,field,value_json FROM field_overrides WHERE fund_id=? ORDER BY price_date,field', (identity,))],
                deleted_dates=[r[0] for r in db.execute('SELECT price_date FROM deleted_dates WHERE fund_id=? ORDER BY price_date', (identity,))])
        if version >= 3:
            row = db.execute('SELECT state FROM fund_state WHERE fund_id=?', (identity,)).fetchone()
            result['state'] = row[0] if row else 'active'
        return result


def begin_lifecycle(repo, code, kind, operation_id, expected_revision):
    with repo.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        identity = fund_id(db, code)
        prior = db.execute('SELECT * FROM maintenance_operations WHERE operation_id=?', (operation_id,)).fetchone()
        if prior:
            if prior['fund_id'] != identity or prior['kind'] != kind:
                raise FundlabError('OPERATION_CONFLICT', '操作编号已用于其他操作。')
            return
        check_revision(db, identity, expected_revision)
        state = db.execute('SELECT state FROM fund_state WHERE fund_id=?', (identity,)).fetchone()
        state = state[0] if state else 'active'
        if state != ('active' if kind == 'trash' else 'trashed'):
            raise FundlabError('FUND_STATE_CONFLICT', '基金状态已变化，请刷新后操作。')
        db.execute('INSERT INTO maintenance_operations VALUES(?,?,?,\'pending\',?)', (operation_id, identity, kind, utc_now()))
        db.execute('INSERT INTO fund_state VALUES(?,?,?) ON CONFLICT(fund_id) DO UPDATE SET state=excluded.state,changed_at=excluded.changed_at',
                   (identity, 'trashing' if kind == 'trash' else 'restoring', utc_now()))
        bump(db, identity)


def finish_lifecycle(repo, operation_id):
    with repo.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        operation = db.execute('SELECT * FROM maintenance_operations WHERE operation_id=?', (operation_id,)).fetchone()
        if operation is None or operation['phase'] == 'done':
            return
        identity = operation['fund_id']
        state = 'trashed' if operation['kind'] == 'trash' else 'active'
        group_ids = [row[0] for row in db.execute('SELECT group_id FROM fund_group_members WHERE fund_id=?', (identity,))]
        db.execute('DELETE FROM fund_group_members WHERE fund_id=?', (identity,))
        for group in group_ids:
            db.execute('UPDATE fund_groups SET revision=revision+1 WHERE id=?', (group,))
        db.execute('UPDATE fund_state SET state=?,changed_at=? WHERE fund_id=?', (state, utc_now(), identity))
        db.execute("UPDATE maintenance_operations SET phase='done' WHERE operation_id=?", (operation_id,))
        event(db, identity, '', operation['kind'], {'operation_id': operation_id}, {'state': state})
        bump(db, identity)


def add_row(repo, code, day, values, expected_revision):
    """用空基础行保存身份，显式输入写覆盖；运行记录明确标记local，非采集。"""
    with repo.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        identity = fund_id(db, code)
        require_active(db, identity)
        check_revision(db, identity, expected_revision)
        if db.execute('SELECT 1 FROM deleted_dates WHERE fund_id=? AND price_date=?', (identity, day)).fetchone():
            raise FundlabError('DATE_DELETED', '请先解除该日期的删除限制。')
        if db.execute('SELECT 1 FROM history WHERE fund_id=? AND price_date=?', (identity, day)).fetchone():
            raise FundlabError('DATE_EXISTS', '该日期已有记录，请使用修改功能。')
        stamp, run = utc_now(), str(uuid4())
        data = {key: None for key in ('close','nav_date','nav','source_premium','turnover','shares','shares_added','shares_change','index_change','calculated_premium')}
        data.update(price_date=day, source_row_key=day, extra_fields={})
        db.execute("INSERT INTO update_runs VALUES(?,?, 'local:add','local',?,?,'succeeded',?)", (run, code, stamp, stamp, dump({'origin': 'manual', 'status': 'succeeded'})))
        db.execute('INSERT INTO history VALUES(?,?,?,?,?,?)', (identity, day, dump(data), '{}', stamp, run))
        for field, value in values.items():
            db.execute('INSERT INTO field_overrides VALUES(?,?,?,?,?)', (identity, day, field, dump(value), stamp))
        event(db, identity, day, 'add', {}, values)
        bump(db, identity)


def change_group(repo, action, *, group_id=None, name=None, codes=(), expected_revision=None):
    with repo.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        if action == 'create':
            try:
                identity = db.execute('INSERT INTO fund_groups(name) VALUES(?)', (name,)).lastrowid
            except sqlite3.IntegrityError:
                raise FundlabError('GROUP_EXISTS', '同名分组已存在。') from None
            return identity
        group = db.execute('SELECT revision FROM fund_groups WHERE id=?', (group_id,)).fetchone()
        if group is None:
            raise FundlabError('NO_GROUP', '分组不存在，请刷新。')
        if type(expected_revision) is not int or group[0] != expected_revision:
            raise FundlabError('DATA_STALE', '分组已变化，请刷新。')
        if action == 'rename':
            try:
                db.execute('UPDATE fund_groups SET name=?,revision=revision+1 WHERE id=?', (name, group_id))
            except sqlite3.IntegrityError:
                raise FundlabError('GROUP_EXISTS', '同名分组已存在。') from None
        elif action == 'delete':
            db.execute('DELETE FROM fund_groups WHERE id=?', (group_id,))
        elif action == 'members':
            identities = [fund_id(db, code) for code in codes]
            for identity in identities:
                require_active(db, identity)
            db.execute('DELETE FROM fund_group_members WHERE group_id=?', (group_id,))
            db.executemany('INSERT INTO fund_group_members VALUES(?,?)', [(group_id, identity) for identity in identities])
            db.execute('UPDATE fund_groups SET revision=revision+1 WHERE id=?', (group_id,))
        else:
            raise FundlabError('VALIDATION_ERROR', '分组操作无效。')
        return group_id
