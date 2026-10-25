"""网页本人登录入口；只验证专用窗口表格，不读取或记录凭据。"""
import time
from urllib.parse import urlsplit

from playwright.sync_api import Error

from fundlab.collectors.session import acquire_session
from fundlab.errors import FundlabError
from fundlab.probe import check_session, DETAIL_URL


def login_session(config, *, progress=None, stop_event=None, wait_seconds=1200) -> dict:
    if not 1 <= wait_seconds <= 7200:
        raise FundlabError("VALIDATION_ERROR", "登录等待时间不正确。")
    if stop_event is not None and stop_event.is_set():
        raise FundlabError("LOGIN_CANCELLED", "登录已取消。")
    with acquire_session(config, interactive=True) as context:
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(DETAIL_URL, wait_until="domcontentloaded")
            if progress:
                progress("waiting_login")
            deadline = time.monotonic() + wait_seconds
            while time.monotonic() < deadline:
                if stop_event is not None and stop_event.is_set():
                    raise FundlabError("LOGIN_CANCELLED", "登录已取消；已保存的数据保持不变。")
                if page.is_closed():
                    raise FundlabError("LOGIN_WINDOW_CLOSED", "登录窗口已关闭，未确认登录成功；可重新打开。")
                if check_session(page) == "HISTORY_VISIBLE":
                    return {"status": "succeeded", "authenticated": True,
                            "message": "专用 Edge 登录已验证，窗口已关闭。现在可以查找或获取基金数据。"}
                location = urlsplit(page.url)
                if location.hostname == "www.jisilu.cn" and location.path in ("/", "/home/"):
                    page.goto(DETAIL_URL, wait_until="domcontentloaded")
                page.wait_for_timeout(1000)
            raise FundlabError("LOGIN_TIMEOUT", "等待登录超时，请重新打开专用 Edge，由本人登录。")
        except Error:
            if stop_event is not None and stop_event.is_set():
                raise FundlabError("LOGIN_CANCELLED", "登录已取消。") from None
            raise FundlabError("LOGIN_BROWSER_ERROR", "登录窗口或网络连接未完成，请重新打开专用 Edge。") from None
