"""Preview and confirm a local import without trusting client-supplied parsed rows."""

from collections import OrderedDict
from copy import deepcopy
from threading import Lock
from time import monotonic
from uuid import uuid4
import sqlite3

from fundlab.errors import FundlabError
from fundlab.parsers.tabular import parse_table
from fundlab.services.locking import update_lock
from fundlab.storage.imports import commit_batch, inspect_target, receipt
from fundlab.storage.repository import Repository
from fundlab.validation import validate_code

PREVIEW_TTL = 900
MAX_PREVIEWS = 4


class DataImportService:
    def __init__(self, config):
        self.config = config
        self._previews = OrderedDict()
        self._lock = Lock()

    def preview(self, code, name, filename, content, *, mapping=None, header_row=1,
                sheet=None, encoding='utf-8-sig', ratio_unit='percent'):
        if not isinstance(code, str) or not isinstance(name, str) or not isinstance(filename, str):
            raise FundlabError('VALIDATION_ERROR', '请输入基金代码、名称和文件名。')
        code, name = validate_code(code), name.strip()
        if not 1 <= len(name) <= 80 or any(ord(char) < 32 for char in name):
            raise FundlabError('VALIDATION_ERROR', '请输入 1 至 80 字的基金名称。')
        parsed = parse_table(filename, content, mapping=mapping, header_row=header_row,
                             sheet=sheet, encoding=encoding, ratio_unit=ratio_unit, code=code)
        records = parsed.pop('records')
        expected = inspect_target(self.config.database_path, code, records)
        fund, actions = expected
        state = fund[3] if fund else 'new'
        token = None
        if not parsed['error_count'] and state in ('new', 'active', 'trashed'):
            token = uuid4().hex
            with self._lock:
                self._previews[token] = (monotonic(), code, name, records, expected)
                while len(self._previews) > MAX_PREVIEWS:
                    self._previews.popitem(last=False)
        return {**parsed, 'token': token, 'code': code, 'name': fund[1] if fund else name,
                'state': state, 'requires_restore': state == 'trashed',
                'inserted': actions.count('insert'), 'skipped_existing': actions.count('existing'),
                'skipped_deleted': actions.count('deleted'),
                'sample': [{'row': row, 'values': deepcopy(values), 'action': action}
                           for (row, values), action in zip(records[:20], actions[:20])]}

    def commit(self, token, *, restore=False):
        if (not isinstance(token, str) or len(token) != 32
                or any(char not in '0123456789abcdef' for char in token) or type(restore) is not bool):
            raise FundlabError('VALIDATION_ERROR', '导入确认参数无效。')
        path = self.config.database_path
        try:
            with update_lock(self.config):
                prior = receipt(path, token)
                if prior is not None:
                    return prior
                with self._lock:
                    preview = self._previews.get(token)
                    if preview and monotonic() - preview[0] >= PREVIEW_TTL:
                        del self._previews[token]
                        preview = None
                if preview is None:
                    raise FundlabError('PREVIEW_STALE', '导入预览已失效，请重新预览。')
                _, code, name, records, expected = preview
                if inspect_target(path, code, records) != expected:
                    raise FundlabError('PREVIEW_STALE', '本地基金数据已变化，请重新预览。')
                fund, _ = expected
                if fund and fund[3] == 'trashed' and not restore:
                    raise FundlabError('FUND_TRASHED', '请确认恢复基金后再导入。')
                result = commit_batch(Repository(path), token, code, name, records, expected, restore=restore)
                with self._lock:
                    self._previews.pop(token, None)
                return result
        except (sqlite3.Error, OSError, RuntimeError) as error:
            raise FundlabError('IMPORT_WRITE_FAILED', '导入未完成，请核对本地数据库后重试。') from error
