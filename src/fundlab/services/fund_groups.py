"""自选分组；归属与自动更新名单独立。"""
from fundlab.errors import FundlabError
from fundlab.services.data_management import prepare_repository_locked
from fundlab.services.locking import update_lock
from fundlab.storage.maintenance import change_group, maintenance_state
from fundlab.validation import validate_code

MAX_GROUP_NAME = 80


class FundGroupService:
    def __init__(self, config):
        self.config = config

    def state(self):
        return maintenance_state(self.config.database_path)['groups']

    def change(self, action, *, group_id=None, name=None, codes=(), expected_revision=None):
        if action not in {'create','rename','delete','members'}:
            raise FundlabError('VALIDATION_ERROR', '分组操作无效。')
        if action in {'create','rename'}:
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= MAX_GROUP_NAME or any(ord(c) < 32 for c in name):
                raise FundlabError('VALIDATION_ERROR', '分组名不能为空、含控制字符或超过80字。')
            name = name.strip()
        if action != 'create' and (type(group_id) is not int or group_id < 1):
            raise FundlabError('VALIDATION_ERROR', '分组编号无效。')
        if not isinstance(codes, (list, tuple)):
            raise FundlabError('VALIDATION_ERROR', '基金列表格式无效。')
        codes = tuple(dict.fromkeys(validate_code(code) for code in codes))
        with update_lock(self.config):
            repo = prepare_repository_locked(self.config)
            identity = change_group(repo, action, group_id=group_id, name=name, codes=codes, expected_revision=expected_revision)
        return {'group_id': identity, 'groups': self.state()}
