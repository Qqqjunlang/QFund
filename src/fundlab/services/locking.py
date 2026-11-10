"""采集、导出与本地维护共享项目写锁；业务编排只在最外层获取一次。"""
from fundlab.collectors.session import ProfileLock
from fundlab.errors import FundlabError


def update_lock(config):
    return ProfileLock(config.local_dir / 'update.lock',
                       FundlabError('UPDATE_BUSY', '已有更新、导出或数据维护进行中，请等待结束后重试。'))


def membership_lock(config):
    """名单只锁名单；允许采集期间停用尚未开始的基金。回收按项目锁→名单锁顺序。"""
    return ProfileLock(config.local_dir / 'watchlist.lock',
                       FundlabError('WATCHLIST_BUSY', '名单正在修改，请稍后刷新重试。'))
