"""阶段一可重复验证，不将重复页或安全上限误判成完整历史。"""
from datetime import datetime, timezone
import hashlib
import json
import time
import re
from urllib.parse import parse_qsl, urlsplit

from fundlab.collectors.session import acquire_session
from fundlab.config import AppConfig
from fundlab.errors import FundlabError
from fundlab.probe import DETAIL_URL, check_session, write_evidence

HISTORY_PATH = "/data/qdii/detail_hists/"  # 已在 513100 页面实际请求中验证
CATALOG_PATHS = {
    "/data/qdii/qdii_list/C": "QDII",
    "/data/qdii/qdii_list/E": "QDII",
    "/data/etf/etf_list/": "ETF",
    "/data/lof/stock_lof_list/": "LOF",
}


def page_fingerprint(rows: list) -> str:
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate_rows(payload: dict) -> list:
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        raise FundlabError("SCHEMA_CHANGED", "历史响应结构改变，未继续采集。")
    for row in payload["rows"]:
        if (not isinstance(row, dict) or not isinstance(row.get("cell"), dict)
                or str(row["cell"].get("fund_id")) != "513100"
                or not row.get("id") or not row["cell"].get("price_dt")):
            raise FundlabError("SCHEMA_CHANGED", "基金或日期字段不符合已验证结构，未继续采集。")
    return payload["rows"]


def verify_history(config: AppConfig, *, max_pages: int = 120) -> dict:
    """保持源页面原有页大小，仅改变页码；当前账号已知第 2 页重复。"""
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "fund_code": "513100",
              "status": "UNVERIFIED", "pages": [], "full_history_verified": False}
    all_rows = []
    with acquire_session(config, interactive=True) as context:
        page = context.new_page()
        observed = {}

        def observe(response):
            if (urlsplit(response.url).hostname == "www.jisilu.cn"
                    and urlsplit(response.url).path == HISTORY_PATH
                    and response.request.resource_type in ("xhr", "fetch")):
                # 仅在内存中复用源页面自己发出的请求。
                observed.update(url=response.url, form=dict(parse_qsl(response.request.post_data or "")))

        page.on("response", observe)
        page.goto(DETAIL_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        if check_session(page) == "AUTH_REQUIRED":
            raise FundlabError("AUTH_REQUIRED", "集思录登录已失效，请运行 login 后重试。")
        if not observed:
            raise FundlabError("SCHEMA_CHANGED", "未观察到已验证的历史请求，不能猜测接口。")
        fingerprints = set()
        for number in range(1, max_pages + 1):
            form = dict(observed["form"])
            form["page"] = str(number)
            try:
                response = page.evaluate("""async ({url,form}) => {
                  const r=await fetch(url,{method:'POST',credentials:'same-origin',
                    headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams(form)});
                  if(!r.ok) return {status:r.status};
                  return {status:r.status,data:await r.json()};
                }""", {"url": observed["url"], "form": form})
            except Exception:
                raise FundlabError("NETWORK_ERROR", "读取历史响应失败，未确认完整历史。") from None
            if response["status"] != 200:
                raise FundlabError("NETWORK_ERROR", f"历史请求返回 HTTP {response['status']}，未确认完整历史。")
            rows = validate_rows(response["data"])
            summary = {"requested_page": number, "returned_page": response["data"].get("page"),
                       "rows": len(rows), "first": rows[0]["cell"]["price_dt"] if rows else None,
                       "last": rows[-1]["cell"]["price_dt"] if rows else None}
            report["pages"].append(summary)
            print(f"请求第 {number} 页：{len(rows)} 条；返回页码 {summary['returned_page']}", flush=True)
            if not rows:
                report["status"] = "EMPTY_PAGE_TERMINUS_SCOPE_ONLY"
                break
            fingerprint = page_fingerprint(rows)
            if fingerprint in fingerprints:
                report["status"] = "REPEATED_PAGE_NOT_COMPLETE"
                break
            fingerprints.add(fingerprint)
            all_rows.extend(rows)
            page.wait_for_timeout(2200)
        else:
            report["status"] = "SAFETY_LIMIT_NOT_COMPLETE"
    dates = [row["cell"]["price_dt"] for row in all_rows]
    report.update(records=len(all_rows), unique_ids=len({r["id"] for r in all_rows}),
                  start=min(dates, default=None), end=max(dates, default=None))
    # 空页只能证明接口可枚举范围结束，不能单独证明成立以来全部历史。
    write_evidence(config, {"fund_code": "513100", "rows": all_rows}, "history-513100")
    destination = write_evidence(config, report, "history-verification")
    print(f"历史验证：{report['status']}；证据：{destination}")
    return report


def verify_catalog(config: AppConfig, query: str, *, quiet: bool = False, persist_evidence: bool = True) -> dict:
    """加载真实类别列表，再在代码和名称上匹配；不使用社区搜索冒充基金搜索。"""
    candidates = {}
    sources = []
    received_paths = set()
    with acquire_session(config, interactive=True) as context:
        page = context.new_page()

        def observe(response):
            parts = urlsplit(response.url)
            family = CATALOG_PATHS.get(parts.path)
            if parts.hostname != "www.jisilu.cn" or not family:
                return
            try:
                payload = response.json()
                rows = payload["rows"]
                if not isinstance(rows, list):
                    return
                parsed = {}
                for row in rows:
                    cell = row["cell"]
                    code, name = str(cell["fund_id"]), cell["fund_nm"]
                    if not re.fullmatch(r"[0-9]{6}", code) or not isinstance(name, str) or not name.strip():
                        return
                    key = (code, family)
                    parsed[key] = {"code": code, "name": name, "category": family}
                candidates.update(parsed)
                received_paths.add(parts.path)
                sources.append({"path": parts.path, "rows": len(rows)})
            except (ValueError, KeyError, TypeError):
                return

        page.on("response", observe)
        for path in ("/data/qdii/", "/data/etf/", "/data/lof/"):
            page.goto("https://www.jisilu.cn" + path, wait_until="domcontentloaded")
            if "/account/login" in page.url:
                raise FundlabError("AUTH_REQUIRED", "分类列表需要登录，请先运行 login。")
            required = {key for key in CATALOG_PATHS if key.startswith(path)}
            deadline = time.monotonic() + 15
            while not required <= received_paths and time.monotonic() < deadline:
                page.wait_for_timeout(200)
    if not sources:
        raise FundlabError("SCHEMA_CHANGED", "未取得已验证分类列表，未使用空缓存代替真实数据。")
    needle = query.strip().casefold()
    matches = [v for v in candidates.values() if v["code"] == needle or needle in v["name"].casefold()]
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "query": query, "sources": sources,
              "scope": "OBSERVED_CATEGORY_LISTS_NOT_ALL_FUNDS", "candidate_count": len(candidates),
              "matches": matches, "missing_sources": sorted(set(CATALOG_PATHS) - received_paths)}
    if persist_evidence:
        write_evidence(config, {"candidates": list(candidates.values())}, "fund-catalog")
        write_evidence(config, report, "catalog-verification")
    if not quiet:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return report
