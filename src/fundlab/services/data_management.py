"""本地维护统一服务；不联网、不重写导出、不注册系统任务。"""
from uuid import uuid4

from fundlab.errors import FundlabError
from fundlab.parsers.history import FIELD_DEFINITIONS, RATIO_FIELDS, parse_date, parse_number
from fundlab.services.locking import update_lock
from fundlab.storage import maintenance
from fundlab.storage.repository import Repository
from fundlab.storage.effective import require_active
from fundlab.validation import validate_code


def recover_pending_locked(config, repo):
    """调用方已持项目锁；跨库中断状态保持不可用，名单移除幂等可重试。"""
    from fundlab.services.watchlist import WatchlistService
    for operation in maintenance.maintenance_state(config.database_path)['pending']:
        with repo.connection() as db:
            code = db.execute('SELECT code FROM funds WHERE id=?', (operation['fund_id'],)).fetchone()[0]
        WatchlistService(config)._remove_locked(code)
        maintenance.finish_lifecycle(repo, operation['operation_id'])


def prepare_repository_locked(config):
    repo = Repository(config.database_path)
    recover_pending_locked(config, repo)
    return repo


def ensure_active_locked(config, repo, code, *, restore=False):
    with repo.connection() as db:
        row = db.execute("SELECT id FROM funds WHERE source='jisilu' AND code=?", (code,)).fetchone()
        if row is None:
            return  # 新基金的真实类别仍由原采集器证明。
        try:
            require_active(db, row[0])
            return
        except FundlabError:
            if not restore:
                raise
    state = maintenance.record_state(config.database_path, code)
    maintenance.begin_lifecycle(repo, code, 'restore', str(uuid4()), state['revision'])
    recover_pending_locked(config, repo)


def input_values(changes):
    """服务输入比例以百分数填写（12.5表示12.5%），存储仍为比例小数。"""
    allowed = {field[0] for field in FIELD_DEFINITIONS} - {'price_date'}
    try:
        if not isinstance(changes, dict) or not changes or changes.keys() - allowed:
            raise ValueError()
        return {key: parse_date(value) if key == 'nav_date' else parse_number(
            value, ratio=key in RATIO_FIELDS, nonnegative=key in {'close','nav','turnover','shares'})
            for key, value in changes.items()}
    except (ValueError, TypeError, ArithmeticError):
        raise FundlabError('VALIDATION_ERROR', '请输入有效字段、数值和日期；缺失不能当零。') from None


class DataManagementService:
    def __init__(self, config):
        self.config = config

    def state(self, code):
        return maintenance.record_state(self.config.database_path, validate_code(code))

    def list_trash(self):
        return maintenance.maintenance_state(self.config.database_path)['trash']

    def recover(self):
        if self.config.database_path.is_file():
            with update_lock(self.config):
                prepare_repository_locked(self.config)
        return self.list_trash()

    def mutate(self, code, day, *, action='edit', changes=None, expected_revision):
        code = validate_code(code)
        try:
            parse_date(day, required=True)
        except (ValueError, TypeError):
            raise FundlabError('VALIDATION_ERROR', '价格日期须为YYYY-MM-DD。') from None
        if action not in {'add','edit','release','delete','restore'}:
            raise FundlabError('VALIDATION_ERROR', '数据维护操作无效。')
        if action in {'add', 'edit'}:
            values = input_values(changes)
        else:
            values = {} if changes is None else changes
        if not self.config.database_path.is_file():
            raise FundlabError('NO_DATA', '请先建立基金本地记录。')
        with update_lock(self.config):
            repo = prepare_repository_locked(self.config)
            if action == 'add':
                maintenance.add_row(repo, code, day, values, expected_revision)
            else:
                repo.apply_mutation(code, day, action=action, changes=values, expected_revision=expected_revision)
        return self.state(code)

    def lifecycle(self, code, *, action, expected_revision, operation_id=None):
        code = validate_code(code)
        if action not in {'trash', 'restore'}:
            raise FundlabError('VALIDATION_ERROR', '回收站操作无效。')
        operation_id = str(uuid4()) if operation_id is None else operation_id
        if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 128:
            raise FundlabError('VALIDATION_ERROR', '操作编号无效。')
        if not self.config.database_path.is_file():
            raise FundlabError('NO_DATA', '本地没有该基金。')
        with update_lock(self.config):
            repo = prepare_repository_locked(self.config)
            maintenance.begin_lifecycle(repo, code, action, operation_id, expected_revision)
            recover_pending_locked(self.config, repo)
        return {**self.state(code), 'operation_id': operation_id}
