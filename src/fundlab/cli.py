"""验证、更新、导出、本地溢价分析与交互图入口。"""
import argparse
from importlib.metadata import version
import json
import platform
import sys

from fundlab.collectors.session import find_edge
from fundlab.config import AppConfig
from fundlab.errors import FundlabError
from fundlab.presentation import APP_NAME
from fundlab.probe import inspect_source
from fundlab.verification import verify_catalog, verify_history
from fundlab.services.search import resolve_name
from fundlab.services.update import retry_export, update_one
from fundlab.services.analysis import analyze_one, create_premium_chart
from fundlab.analysis.calculations import AnalysisRequest
from fundlab.storage.repository import Repository
from fundlab.validation import validate_code


def main() -> int:
    # Windows 重定向输出时也统一 UTF-8，避免中文验证记录乱码。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=f"{APP_NAME}：真实数据采集、本地导出、溢价分析与交互图")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="检查 Python、Playwright 和 Edge")
    login = sub.add_parser("login", help="打开专用 Edge，由本人登录并验证表格")
    login.add_argument("--wait-seconds", type=int, default=1200)
    inspect = sub.add_parser("inspect", help="验证已保存会话，只读取 513100 首屏")
    inspect.add_argument("--headed", action="store_true", help="显示窗口，用于比较有窗口与无窗口的会话行为")
    history = sub.add_parser("verify-history", help="以有窗口 Edge 验证 513100 分页；重复页会明确报未完成")
    history.add_argument("--max-pages", type=int, default=120)
    catalog = sub.add_parser("verify-catalog", help="验证已观察分类列表的代码或名称匹配")
    catalog.add_argument("query", help="例如 513100 或 纳指")
    search = sub.add_parser("search", help="按代码或名称查找真实基金候选")
    search.add_argument("query")
    update = sub.add_parser("update", help="手动更新单只 QDII 基金并累积保存、导出")
    identifier = update.add_mutually_exclusive_group()
    identifier.add_argument("--fund", help="六位基金代码")
    identifier.add_argument("--name", help="基金名称；多个候选时会要求明确选择代码")
    update.add_argument("--mode", choices=("initial", "incremental", "full"), default="incremental",
                        help="full 仅重读源站可访问窗口，不删除本地累计历史")
    export = sub.add_parser("export", help="仅重试本地导出，不联网")
    export.add_argument("--fund", required=True)
    runs = sub.add_parser("runs", help="查看本地运行记录，不联网")
    runs.add_argument("--limit", type=int, default=20)
    status = sub.add_parser("status", help="查看单只基金的本地数据范围，不联网")
    status.add_argument("--fund", required=True)
    schedule = sub.add_parser("schedule", help="查看计划、管理Windows任务或显式执行一次")
    schedule_sub = schedule.add_subparsers(dest="schedule_command", required=True)
    schedule_sub.add_parser("status", help="只读查看北京时间到期计划，不采集")
    for operation, help_text in [('install', '安装或启用本项目Windows定时任务'), ('disable', '停用本项目Windows定时任务'),
                                  ('remove', '移除本项目Windows任务，保留数据')]:
        schedule_sub.add_parser(operation, help=help_text)
    set_time = schedule_sub.add_parser("set-time", help="保存所有名单基金共用的北京时间，不采集")
    set_time.add_argument("update_time", help="HH:MM，例如20:30")
    due = schedule_sub.add_parser("run-due", help="执行一次到期更新或补跑，不安装任务")
    due.add_argument("--retry-failed", action="store_true", help="本人处理登录等原因后显式重试，仍受每时段3次上限约束")
    chart = sub.add_parser("chart", help="用本地已保存数据生成交互式溢价率时序图，不联网")
    chart.add_argument("--fund", required=True, help="六位基金代码")
    analyze = sub.add_parser("analyze", help="本地溢价率分位与 N 日平均溢价率计算，不联网")
    analyze.add_argument("--fund", required=True)
    analyze.add_argument("--period", choices=("1m", "3m", "6m", "1y", "all", "custom"), default="all")
    analyze.add_argument("--start", help="custom 区间起始日期 YYYY-MM-DD")
    analyze.add_argument("--end", help="custom 区间结束日期 YYYY-MM-DD")
    analyze.add_argument("--ma", type=int, nargs="+", default=[20, 60], metavar="N", help="N 日平均溢价率周期，默认 20 60")
    analyze.add_argument("--premium", help="额外计算指定溢价率的分位，例如 12.5 表示 12.5%%；默认比较最新有效溢价")
    args = parser.parse_args()
    config = AppConfig.default()
    if args.command == "doctor":
        edge = find_edge()
        print(json.dumps({"python": platform.python_version(), "executable": sys.executable,
                          "playwright": version("playwright"), "edge": str(edge) if edge else None,
                          "project": str(config.root), "profile": str(config.profile_dir)},
                         ensure_ascii=False, indent=2))
        return 0 if edge else 1
    try:
        if args.command == "schedule":
            from fundlab.scheduling.runner import run_due_updates, schedule_status
            if args.schedule_command in {'install', 'disable', 'remove'}:
                from fundlab.scheduling.windows_task import WindowsTask
                print(json.dumps(WindowsTask(config).change(args.schedule_command), ensure_ascii=False, indent=2))
                return 0
            if args.schedule_command == "set-time":
                from fundlab.scheduling.settings import ScheduleSettings
                ScheduleSettings(config).save(args.update_time)
            result = (schedule_status(config) if args.schedule_command in {"status", "set-time"} else
                      run_due_updates(config, retry_failed=args.retry_failed))
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
            return 0 if args.schedule_command in {"status", "set-time"} or result["status"] in {"succeeded", "not_due", "skipped_empty", "skipped_closed"} else 2
        if args.command == "analyze":
            result = analyze_one(config, args.fund, AnalysisRequest(args.period, args.start, args.end, tuple(args.ma), args.premium))
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
            return 0
        if args.command == "chart":
            result = create_premium_chart(config, args.fund)
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
            return 0
        if args.command in {"update", "export"}:
            if args.command == "export":
                result = retry_export(config, args.fund)
            else:
                code = args.fund
                if code is None:
                    query = args.name or input("请输入基金代码或名称：").strip()
                    code = query if query.isascii() and query.isdigit() and len(query) == 6 else resolve_name(config, query)
                result = update_one(config, code, mode=args.mode)
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            return 0 if result.status == "succeeded" else 3 if result.status == "export_failed" else 1
        if args.command in {"runs", "status"}:
            if not config.database_path.exists():
                raise FundlabError("NO_DATA", "还没有本地数据，请先执行 update。")
            repo = Repository(config.database_path)
            if args.command == "runs":
                if not 1 <= args.limit <= 200:
                    parser.error("记录条数必须在 1—200 之间")
                items = repo.list_runs(args.limit)
                for item in items:
                    if item["result_json"]:
                        item["result"] = json.loads(item.pop("result_json"))
                print(json.dumps(items, ensure_ascii=False, indent=2))
            else:
                code = validate_code(args.fund)
                fund = repo.get_fund(code)
                if not fund:
                    raise FundlabError("NO_DATA", "本地没有该基金，请先更新。")
                rows = repo.query_history(code)
                print(json.dumps({"code": code, "name": fund.name, "rows": len(rows),
                                  "start": rows[-1]["price_date"] if rows else None,
                                  "end": rows[0]["price_date"] if rows else None,
                                  "coverage": repo.latest_coverage(code)}, ensure_ascii=False, indent=2))
            return 0
        if args.command == "search":
            result = verify_catalog(config, args.query, persist_evidence=False)
            return 0 if result["matches"] else 2
        if args.command == "verify-history":
            if not 1 <= args.max_pages <= 120:
                parser.error("页数上限必须在 1—120 之间")
            result = verify_history(config, max_pages=args.max_pages)
            return 0 if result["full_history_verified"] else 2
        if args.command == "verify-catalog":
            if not args.query.strip():
                parser.error("查询词不能为空")
            result = verify_catalog(config, args.query)
            return 0 if result["matches"] else 2
        if args.command == "login" and not 1 <= args.wait_seconds <= 7200:
            parser.error("等待秒数必须在 1—7200 之间")
        result = inspect_source(config, login=args.command == "login",
                                wait_seconds=getattr(args, "wait_seconds", 1200),
                                headed=getattr(args, "headed", False))
        return 0 if result["status"] == "HISTORY_VISIBLE" else 2
    except FundlabError as exc:
        print(f"{exc.code}：{exc}", file=sys.stderr)
        return 1
    except EOFError:
        print("VALIDATION_ERROR：请使用 --fund 六位代码，或 --name 基金名称。", file=sys.stderr)
        return 1
    except Exception:
        print("OPERATION_FAILED：操作未全部完成，请检查网络、磁盘和任务记录；不要将本次结果当作全部成功。", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已停止验证，登录状态保留在本机。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
