"""基础行情、手动字段覆盖与有效溢价的唯一合并规则。"""
from decimal import Decimal, localcontext
import json

from fundlab.parsers.history import canonical_decimal, FIELD_DEFINITIONS


def premium_value(row):
    return row['effective_premium'] if 'effective_premium' in row else row['source_premium']


def require_active(db, fund_id):
    """缺省为历史基金active；过渡状态一律禁止普通读写。"""
    from fundlab.errors import FundlabError
    if db.execute('PRAGMA user_version').fetchone()[0] >= 3:
        state = db.execute('SELECT state FROM fund_state WHERE fund_id=?', (fund_id,)).fetchone()
        if state and state[0] != 'active':
            raise FundlabError('FUND_TRASHED', '该基金在回收站或正在维护，请先确认恢复后再操作。')


def effective_rows(db, fund_id, version):
    overrides, deleted = {}, set()
    revision = 0
    if version >= 2:
        for row in db.execute('SELECT price_date,field,value_json FROM field_overrides WHERE fund_id=?', (fund_id,)):
            overrides.setdefault(row[0], {})[row[1]] = json.loads(row[2])
        deleted = {row[0] for row in db.execute('SELECT price_date FROM deleted_dates WHERE fund_id=?', (fund_id,))}
        found = db.execute('SELECT revision FROM data_revisions WHERE fund_id=?', (fund_id,)).fetchone()
        revision = found[0] if found else 0
    rows = []
    for stored in db.execute('''SELECT h.price_date,h.data_json,h.fetched_at,u.trigger FROM history h
            JOIN update_runs u ON u.run_id=h.last_run_id WHERE h.fund_id=? ORDER BY h.price_date''', (fund_id,)):
        if stored[0] in deleted:
            continue
        data = {**json.loads(stored[1]), 'fetched_at': stored[2]}
        base_origin = {'local:add': 'manual', 'local:import': 'import'}.get(stored[3], 'jisilu')
        fields = overrides.get(stored[0], {})
        data.update(fields)
        derived = None
        if data['close'] is not None and data['nav'] is not None and Decimal(data['nav']) > 0:
            with localcontext() as context:
                context.prec = 100
                derived = canonical_decimal(Decimal(data['close']) / Decimal(data['nav']) - 1)
        if 'source_premium' in fields:
            value, origin = data['source_premium'], 'manual'
        elif {'close', 'nav'} & fields.keys():
            value, origin = derived, 'derived'
        else:
            value, origin = data['source_premium'], base_origin
        if {'close', 'nav'} & fields.keys():
            data['calculated_premium'] = derived
        data.update(effective_premium=value, premium_origin=origin,
                    protected_fields=sorted(fields), revision=revision,
                    record_origin=base_origin,
                    field_origins={key: 'manual' if key in fields else base_origin for key, *_ in FIELD_DEFINITIONS})
        rows.append(data)
    return rows
