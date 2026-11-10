"""HTTP contracts for local fund maintenance and import."""

from base64 import b64decode
from binascii import Error

from fundlab.errors import FundlabError
from fundlab.parsers.history import FIELD_DEFINITIONS
from fundlab.parsers.tabular import MAX_BYTES
from fundlab.services.data_import import DataImportService
from fundlab.services.data_management import DataManagementService
from fundlab.services.fund_groups import FundGroupService

PREVIEW_PATH = '/api/import/preview'
PREVIEW_BODY_LIMIT = 4 * ((MAX_BYTES + 2) // 3) + 4096

POST_FIELDS = {
    PREVIEW_PATH: ({'code', 'name', 'filename', 'content'}, {'mapping', 'header_row', 'sheet', 'encoding', 'ratio_unit'}),
    '/api/import/commit': ({'token'}, {'restore'}),
    '/api/maintenance/row': ({'code', 'day', 'action', 'expected_revision'}, {'changes'}),
    '/api/maintenance/fund': ({'code', 'action', 'expected_revision'}, {'operation_id'}),
    '/api/maintenance/recover': (set(), set()),
    '/api/groups/change': ({'action'}, {'group_id', 'name', 'codes', 'expected_revision'}),
}


class ManagementRoutes:
    def __init__(self, config):
        self.data = DataManagementService(config)
        self.groups = FundGroupService(config)
        self.imports = DataImportService(config)

    def get(self, path, params):
        if path == '/api/maintenance':
            if set(params) != {'code'} or len(params['code']) != 1:
                raise ValueError()
            return self.data.state(params['code'][0])
        if path in ('/api/trash', '/api/groups', '/api/import/fields'):
            if params:
                raise ValueError()
            if path == '/api/trash':
                return self.data.list_trash()
            if path == '/api/groups':
                return self.groups.state()
            return {'max_bytes': MAX_BYTES, 'fields': [{'key': key, 'label': title}
                    for key, _, title, _ in FIELD_DEFINITIONS]}
        return None

    def post(self, path, data):
        required, optional = POST_FIELDS[path]
        if not required <= data.keys() <= required | optional:
            raise ValueError()
        if path == PREVIEW_PATH:
            encoded = data.pop('content')
            if not isinstance(encoded, str):
                raise ValueError()
            try:
                content = b64decode(encoded, validate=True)
            except (ValueError, Error):
                raise FundlabError('VALIDATION_ERROR', '无法读取上传的文件内容。') from None
            return self.imports.preview(content=content, **data)
        if path == '/api/import/commit':
            return self.imports.commit(**data)
        if path == '/api/maintenance/row':
            return self.data.mutate(**data)
        if path == '/api/maintenance/fund':
            return self.data.lifecycle(**data)
        if path == '/api/maintenance/recover':
            return self.data.recover()
        return self.groups.change(**data)
