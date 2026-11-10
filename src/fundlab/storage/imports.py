"""Snapshot and commit a single-fund import against the same conflict rules."""

import json

from fundlab.errors import FundlabError
from fundlab.models import utc_now
from fundlab.storage.maintenance import bump, event, read_connection
from fundlab.storage.repository import dump

IMPORT_TRIGGER = 'local:import'


def target_snapshot(db, code, records):
    fund = db.execute("SELECT id,name FROM funds WHERE source='jisilu' AND code=?", (code,)).fetchone()
    if fund is None:
        return None, tuple('insert' for _ in records)
    identity = fund['id']
    version = db.execute('PRAGMA user_version').fetchone()[0]
    existing = {row[0] for row in db.execute('SELECT price_date FROM history WHERE fund_id=?', (identity,))}
    deleted, revision, state = set(), 0, 'active'
    if version >= 2:
        deleted = {row[0] for row in db.execute('SELECT price_date FROM deleted_dates WHERE fund_id=?', (identity,))}
        row = db.execute('SELECT revision FROM data_revisions WHERE fund_id=?', (identity,)).fetchone()
        revision = row[0] if row else 0
    if version >= 3:
        row = db.execute('SELECT state FROM fund_state WHERE fund_id=?', (identity,)).fetchone()
        state = row[0] if row else 'active'
    actions = []
    for _, record in records:
        day = record['price_date']
        actions.append('deleted' if day in deleted else 'existing' if day in existing else 'insert')
    return (identity, fund['name'], revision, state), tuple(actions)


def inspect_target(path, code, records):
    if not path.is_file():
        return None, tuple('insert' for _ in records)
    with read_connection(path) as db:
        return target_snapshot(db, code, records)


def _receipt(db, token):
    row = db.execute('SELECT result_json FROM update_runs WHERE run_id=? AND trigger=?',
                     (token, IMPORT_TRIGGER)).fetchone()
    return json.loads(row[0]) if row else None


def receipt(path, token):
    if not path.is_file():
        return None
    with read_connection(path) as db:
        return _receipt(db, token)


def commit_batch(repo, token, code, name, records, expected, *, restore):
    with repo.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        prior = _receipt(db, token)
        if prior is not None:
            return prior
        current = target_snapshot(db, code, records)
        if current != expected:
            raise FundlabError('PREVIEW_STALE', '本地基金数据已变化，请重新预览。')
        fund, actions = current
        state = fund[3] if fund else 'active'
        if state != 'active' and (state != 'trashed' or not restore):
            raise FundlabError('FUND_TRASHED', '请确认恢复基金后再导入。')
        incoming = [record for (_, record), action in zip(records, actions) if action == 'insert']
        result = {'code': code, 'inserted': len(incoming), 'skipped_existing': actions.count('existing'),
                  'skipped_deleted': actions.count('deleted'), 'restored': state == 'trashed'}
        stamp = utc_now()
        if fund is None:
            identity = db.execute("INSERT INTO funds(source,code,name,category,source_url) VALUES('jisilu',?,?,?, '')",
                                  (code, name, '未验证')).lastrowid
        else:
            identity = fund[0]
        db.execute("""INSERT INTO update_runs(run_id,code,trigger,mode,started_at,completed_at,status,result_json)
            VALUES(?,?,?,'local',?,?,'succeeded',?)""", (token, code, IMPORT_TRIGGER, stamp, stamp, dump(result)))
        if state == 'trashed':
            # Fully trashed funds have already left the watchlist and all groups.
            # Restore in the same transaction as import so a failed batch changes neither.
            db.execute("UPDATE fund_state SET state='active',changed_at=? WHERE fund_id=?", (stamp, identity))
            event(db, identity, '', 'restore', {'state': state}, {'state': 'active', 'operation_id': token})
        db.executemany('INSERT INTO history VALUES(?,?,?,?,?,?)', (
            (identity, record['price_date'], dump({**record, 'calculated_premium': None,
              'source_row_key': record['price_date'], 'extra_fields': {}}), '{}', stamp, token)
            for record in incoming))
        if incoming or state == 'trashed':
            bump(db, identity)
        return result
