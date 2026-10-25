"""阶段一只读验证：记录页面结构和行情响应形状，不记录 cookie、请求头或密码。"""
from datetime import date, datetime, timezone
import json
from pathlib import Path
import time
from urllib.parse import parse_qsl, urlsplit

from playwright.sync_api import Error

from fundlab.collectors.session import acquire_session
from fundlab.config import AppConfig

DETAIL_URL = "https://www.jisilu.cn/data/qdii/detail/513100"


def safe_url(url: str) -> dict:
    parsed = urlsplit(url)
    path = parsed.path
    # 登录重定向路径可能包含编码数据，只保留固定登录入口。
    if path.startswith("/account/"):
        path = "/account/"
    return {"host": parsed.hostname, "path": path,
            "query_keys": sorted({key for key, _ in parse_qsl(parsed.query)})}


def response_shape(payload) -> dict:
    """只报告结构、行字段及记录数，不序列化未知响应中的个人数据。"""
    if not isinstance(payload, dict):
        return {"type": type(payload).__name__}
    result = {"keys": sorted(str(key) for key in payload)}
    for key in ("page", "total", "records"):
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            result[key] = value
    rows = payload.get("rows")
    if isinstance(rows, list):
        result["row_count"] = len(rows)
        if rows and isinstance(rows[0], dict):
            result["row_keys"] = sorted(rows[0])
            cell = rows[0].get("cell")
            if isinstance(cell, dict):
                result["cell_keys"] = sorted(cell)
    return result


def capture_page(page) -> dict:
    # 只提取基金页的表格和导航结构，禁止读取登录输入框的值。
    return page.evaluate("""() => ({
      title: document.title,
      tables: Array.from(document.querySelectorAll('table')).map(t => ({
        id: t.id, class_name: t.className,
        headers: Array.from(t.querySelectorAll('th')).map(x => x.innerText.trim()),
        rows: Array.from(t.querySelectorAll('tbody tr')).slice(0, 8).map(r =>
          Array.from(r.querySelectorAll('td')).map(c => c.innerText.trim()))
      })).filter(t => t.headers.length || t.rows.length),
      links: Array.from(document.querySelectorAll('a[href]')).filter(a =>
        a.origin === location.origin && a.pathname.startsWith('/data/'))
        .map(a => ({text:a.innerText.trim(),path:a.pathname})).slice(0, 150),
      scripts: Array.from(document.querySelectorAll('script[src]')).map(s => s.src),
      search_inputs: Array.from(document.querySelectorAll('input')).filter(e =>
        e.type !== 'password' && /search|搜索|代码|名称/i.test(e.id+' '+e.name+' '+e.placeholder))
        .map(e => ({id:e.id,name:e.name,placeholder:e.placeholder})),
      pagination: Array.from(document.querySelectorAll('[class*=pager], [id*=pager], [class*=pagination]'))
        .map(e => ({id:e.id,class_name:e.className,text:e.innerText.trim()}))
    })""")


def check_session(page) -> str:
    if "/account/login" in page.url:
        return "AUTH_REQUIRED"
    table = page.locator("#etf_hists")
    if table.count() == 0:
        return "UNVERIFIED"
    headers = table.locator("th").all_text_contents()
    joined = " ".join(headers)
    if "净值" not in joined or "溢价" not in joined:
        return "UNVERIFIED"
    # 表头可能先于数据加载，也可能保留在错误空态中；必须有真实日期行。
    rows = table.locator("tbody tr").evaluate_all(
        "rows => rows.map(row => Array.from(row.querySelectorAll('td')).map(cell => cell.innerText.trim()))"
    )
    for cells in rows:
        if len(cells) != 10:
            continue
        try:
            price_date = date.fromisoformat(cells[0])
        except (ValueError, TypeError):
            continue
        if cells[0] == price_date.isoformat():
            return "HISTORY_VISIBLE"
    return "UNVERIFIED"


def write_evidence(config: AppConfig, evidence: dict, label: str) -> Path:
    config.evidence_dir.mkdir(parents=True, exist_ok=True)
    path = config.evidence_dir / f"{label}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path


def inspect_source(config: AppConfig, *, login: bool = False, wait_seconds: int = 1200,
                   headed: bool = False) -> dict:
    evidence = {"checked_at": datetime.now(timezone.utc).isoformat(), "fund_code": "513100",
                "interactive": login or headed, "network": [], "status": "UNVERIFIED"}
    with acquire_session(config, interactive=login or headed) as context:
        cookies = context.cookies([DETAIL_URL])
        evidence["session_diagnostics"] = {"cookie_count": len(cookies),
                                           "session_cookie_count": sum(c["expires"] == -1 for c in cookies)}
        page = context.pages[0] if context.pages else context.new_page()

        def record_response(response):
            parsed = urlsplit(response.url)
            if parsed.hostname != "www.jisilu.cn" or not parsed.path.startswith("/data/"):
                return
            if response.request.resource_type not in ("xhr", "fetch"):
                return
            item = {"url": safe_url(response.url), "method": response.request.method,
                    "status": response.status}
            post = response.request.post_data
            if post:
                item["form_keys"] = sorted({key for key, _ in parse_qsl(post)})
            try:
                item["shape"] = response_shape(response.json())
            except (Error, ValueError):
                item["shape"] = {"type": "non-json"}
            evidence["network"].append(item)

        page.on("response", record_response)
        try:
            page.goto(DETAIL_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            if login:
                print("请在新打开的专用 Edge 中登录集思录，然后打开 513100 详情页。无需向工具提供密码。", flush=True)
                print("检测到历史表格后将保存验证结果并自动关闭此专用窗口。", flush=True)
                deadline = time.monotonic() + wait_seconds
                while time.monotonic() < deadline and not page.is_closed():
                    if check_session(page) == "HISTORY_VISIBLE":
                        break
                    # 登录后若留在首页，返回已知详情页；不干预尚未完成的登录。
                    location = urlsplit(page.url)
                    if location.hostname == "www.jisilu.cn" and location.path in ("/", "/home/"):
                        page.goto(DETAIL_URL, wait_until="domcontentloaded")
                    page.wait_for_timeout(2000)
            if page.is_closed():
                evidence["status"] = "WINDOW_CLOSED"
            else:
                evidence["status"] = check_session(page)
                evidence["location"] = safe_url(page.url)
                if evidence["status"] != "AUTH_REQUIRED":
                    evidence["page"] = capture_page(page)
                    evidence["page"]["scripts"] = [safe_url(url) for url in evidence["page"]["scripts"]]
            if evidence["status"] == "HISTORY_VISIBLE":
                evidence["completeness"] = "FIRST_PAGE_ONLY_NOT_FULL_HISTORY"
        except Error:
            evidence["status"] = "NETWORK_OR_BROWSER_ERROR"
    label = "login" if login else "inspect-headed" if headed else "inspect-headless"
    path = write_evidence(config, evidence, label)
    print(f"验证状态：{evidence['status']}；本地记录：{path}", flush=True)
    return evidence
