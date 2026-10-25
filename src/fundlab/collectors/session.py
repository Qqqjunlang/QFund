"""专用 Edge 持久会话。绝不复制日常浏览器的 cookie 或用户目录。"""
from contextlib import contextmanager
import os
from pathlib import Path
import sys

from playwright.sync_api import Error, sync_playwright

from fundlab.config import AppConfig
from fundlab.errors import FundlabError, SessionBusy


class ProfileLock:
    """操作系统文件锁；进程退出后自动释放，锁文件本身可以保留。"""

    def __init__(self, path: Path, busy_error: FundlabError | None = None):
        self.path = path
        self.handle = None
        self.busy_error = busy_error

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            raise (self.busy_error or SessionBusy()) from None
        return self

    def __exit__(self, *_):
        if self.handle:
            self.handle.close()
            self.handle = None


def find_edge() -> Path | None:
    candidates = [
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Edge/Application/msedge.exe",
    ]
    return next((path for path in candidates if path.is_file()), None)


@contextmanager
def acquire_session(config: AppConfig, *, interactive: bool = False):
    """不启动日常 profile；登录时有窗口，检查时默认无窗口。"""
    edge = find_edge()
    if edge is None:
        raise FundlabError("EDGE_NOT_FOUND", "未找到 Edge，请确认本机已安装 Microsoft Edge。")
    config.profile_dir.mkdir(parents=True, exist_ok=True)
    with ProfileLock(config.local_dir / "edge-profile.lock"):
        with sync_playwright() as playwright:
            try:
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(config.profile_dir),
                    executable_path=str(edge),
                    headless=not interactive,
                    locale="zh-CN",
                    timezone_id="Asia/Shanghai",
                    viewport={"width": 1360, "height": 900},
                    accept_downloads=False,
                    timeout=30000,
                )
            except Error:
                raise FundlabError(
                    "BROWSER_START_FAILED",
                    "专用 Edge 启动失败，请检查浏览器是否已关闭、系统策略或运行权限；未读取日常登录资料。",
                ) from None
            try:
                context.set_default_timeout(15000)
                context.set_default_navigation_timeout(45000)
                yield context
            finally:
                try:
                    context.close()
                except Error:
                    pass
