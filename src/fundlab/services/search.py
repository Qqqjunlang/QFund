"""名称检索后明确选择；不自动猜测多个候选中哪只最接近。"""
from fundlab.config import AppConfig
from fundlab.errors import FundlabError
from fundlab.validation import validate_query
from fundlab.verification import verify_catalog
from fundlab.validation import validate_code
from fundlab.storage.repository import read_history_snapshot


def search_candidates(config: AppConfig, query: str) -> dict:
    """网页明确选取候选；代码可直接进入已验证路径，名称读取真实目录。"""
    query = validate_query(query)
    if query.isdecimal():
        code = validate_code(query)
        name, category = "待采集时确认名称", "QDII 历史路径"
        try:
            fund = read_history_snapshot(config.database_path, code).fund
            name, category = fund.name, fund.category
        except FundlabError as error:
            if error.code != "NO_DATA":
                raise
        return {"query": query, "matches": [{"code": code, "name": name, "category": category}],
                "partial": False, "message": "代码已确认；名称与历史数据将在获取时由真实页面核实。"}
    report = verify_catalog(config, query, quiet=True, persist_evidence=False)
    partial = bool(report.get("missing_sources"))
    return {"query": query, "matches": [{key: item[key] for key in ("code", "name", "category")} for item in report["matches"]],
            "partial": partial,
            "message": ("部分基金列表未完整加载，以下仅为已读到的候选；可改用准确代码。" if partial else
                        "请选择要获取的基金。" if report["matches"] else "未找到匹配基金，请更换名称或使用准确代码。")}


def resolve_name(config: AppConfig, query: str) -> str:
    query = validate_query(query)
    report = verify_catalog(config, query, quiet=True, persist_evidence=False)
    if report.get("missing_sources"):
        raise FundlabError("CATALOG_INCOMPLETE", "部分基金列表未完整加载，不能确认名称唯一性，请稍后重试或使用六位代码。")
    candidates = {match["code"]: match for match in report["matches"]}
    if not candidates:
        raise FundlabError("FUND_NOT_FOUND", "当前已观察列表中没有匹配的基金；可改用准确基金代码。")
    if len(candidates) > 1:
        choices = "\n".join(f"{fund['code']}  {fund['name']}  {fund['category']}" for fund in candidates.values())
        raise FundlabError("AMBIGUOUS_FUND", "名称匹配到多只基金，请确认后使用 update --fund 六位代码：\n" + choices)
    return next(iter(candidates))
