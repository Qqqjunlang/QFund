"""读取网页自己返回的当前历史窗口；不回退到旧证据，不假称完整历史。"""
from urllib.parse import parse_qsl, urlsplit
import re

from playwright.sync_api import Error, TimeoutError as BrowserTimeout

from fundlab.collectors.session import acquire_session
from fundlab.config import AppConfig
from fundlab.errors import FundlabError
from fundlab.models import Fund, SourceWindow, utc_now
from fundlab.validation import validate_code

HISTORY_PATH = "/data/qdii/detail_hists/"


def fetch_window(config: AppConfig, code: str) -> SourceWindow:
    code = validate_code(code)
    url = f"https://www.jisilu.cn/data/qdii/detail/{code}"
    with acquire_session(config, interactive=True) as context:
        page = context.new_page()

        def is_history(response):
            parts = urlsplit(response.url)
            if (parts.hostname != "www.jisilu.cn" or parts.path != HISTORY_PATH
                    or response.request.method != "POST"):
                return False
            return dict(parse_qsl(response.request.post_data or "")).get("fund_id") == code

        try:
            with page.expect_response(is_history, timeout=30000) as pending:
                navigation = page.goto(url, wait_until="domcontentloaded")
                if "/account/login" in page.url:
                    raise FundlabError("AUTH_REQUIRED", "集思录登录已失效，请双击 scripts\\login.cmd 登录后再更新。")
                if navigation and navigation.status >= 400:
                    raise FundlabError("UNSUPPORTED_CATEGORY", "该代码的 QDII 详情页不可访问，第二阶段尚未接入其他历史页面。")
            response = pending.value
            if response.status == 429:
                raise FundlabError("RATE_LIMITED", "数据源限流，请稍后重试；本地历史未被删除。")
            if response.status != 200:
                raise FundlabError("NETWORK_ERROR", f"历史请求返回 HTTP {response.status}，本次未保存。")
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
                raise FundlabError("SCHEMA_CHANGED", "历史响应字段改变，本次未保存。")
            title = page.title()
            match = re.search(rf"QDII基金\s*-\s*{re.escape(code)}\s*-\s*(.+?)\s*-\s*集思录", title)
            if not match:
                raise FundlabError("SCHEMA_CHANGED", "无法从真实页面确认基金名称和类别，本次未保存。")
            # 等待当前表格真正加载，顺带验证阶段一补强后的可读判断。
            page.wait_for_selector("#etf_hists tbody tr td", timeout=15000)
            from fundlab.probe import check_session
            if check_session(page) != "HISTORY_VISIBLE":
                raise FundlabError("NO_DATA", "页面没有可确认的十列历史记录，本次未保存。")
            rows = payload["rows"]
            coverage = {"kind": "source_window", "source_rows": len(rows),
                        "returned_page": payload.get("page"), "full_history_verified": False,
                        "note": "只保存源站当前可访问窗口；更新会保留本地累计历史，不代表成立以来完整数据。"}
            return SourceWindow(Fund(code, match.group(1).strip(), "QDII", url), rows, utc_now(), coverage)
        except BrowserTimeout:
            if "/account/login" in page.url:
                raise FundlabError("AUTH_REQUIRED", "登录已失效，请重新登录专用 Edge。") from None
            raise FundlabError("SOURCE_TIMEOUT", "未及时取得已验证的 QDII 历史表格，请检查网络或该类别的支持情况。") from None
        except Error:
            raise FundlabError("NETWORK_ERROR", "专用 Edge 或网络读取失败，本次未保存；可稍后重试。") from None
