"""EXE原型入口；复用工作台，打包检查不触发业务或系统任务。"""
import argparse
from importlib.metadata import version
from importlib.resources import files
import hashlib
import json
from pathlib import Path
import sys

from fundlab.config import AppConfig
from fundlab.errors import FundlabError
from fundlab.launcher import check_environment, launch
from fundlab.installation import runtime_guard


def diagnose(config, probe_port=None):
    check_environment()
    from fundlab.collectors.session import find_edge
    from fundlab.scheduling.calendar import load_calendar
    from playwright.sync_api import sync_playwright
    from plotly.offline import get_plotlyjs
    import plotly.graph_objects as go
    import plotly.io as pio
    load_calendar()
    resources = ("analysis/premium_client.js", "web/static/index.html", "web/static/style.css",
                 "web/static/app.js", "web/static/qfund-icon.svg", "scheduling/calendar-2026.json",
                 "scheduling/windows_task.ps1")
    hashes = {name: hashlib.sha256(files('fundlab').joinpath(name).read_bytes()).hexdigest()
              for name in resources}
    plotly_js_size = len(get_plotlyjs())
    # 空图仅验证Plotly动态模块与校验资源，不创建样例行情或保存文件。
    empty_chart_ready = bool(pio.to_html(go.Figure(go.Scatter(x=[], y=[])), include_plotlyjs=True))
    # 只启动包内Node驱动握手，不启动浏览器或使用专用profile。
    with sync_playwright() as playwright:
        driver_ready = playwright.chromium is not None
    result = dict(app="QFund", version=version('fundlab-local'), frozen=bool(getattr(sys, 'frozen', False)),
                data_root=str(config.root), resources=hashes, edge_found=find_edge() is not None,
                playwright_driver_ready=driver_ready, plotly_js_size=plotly_js_size,
                empty_chart_ready=empty_chart_ready,
                automatic_tasks_supported=True)
    if probe_port is not None:
        from urllib.request import build_opener, ProxyHandler
        from fundlab.web.identity import service_identity
        result['expected_identity'] = service_identity(config)
        try:
            with build_opener(ProxyHandler({})).open('http://127.0.0.1:%d/api/health' % probe_port, timeout=2) as response:
                result['local_probe'] = json.loads(response.read(4097))
        except Exception as error:
            import traceback
            result['local_probe_error'] = type(error).__name__ + ': ' + str(error)
            result['local_probe_traceback'] = traceback.format_exc()
    return result


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description="QFund EXE原型（浏览器工作台）")
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true', help='不自动打开浏览器')
    parser.add_argument('--data-root', type=Path, help='技术验证用：显式指定独立数据根目录，不迁移数据')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--diagnose', action='store_true', help='只检查包资源与驱动，不获取数据或管理任务')
    action.add_argument('--schedule-action', choices=('status', 'install', 'disable', 'remove'), help='技术入口：查看或管理本数据目录的Windows自动获取任务')
    parser.add_argument('--probe-port', type=int, help='诊断用：读取指定本机端口的服务识别信息')
    args = parser.parse_args(argv)
    try:
        if args.probe_port is not None and (not args.diagnose or not 1 <= args.probe_port <= 65535):
            raise FundlabError('INVALID_PORT', 'probe-port须配合diagnose，端口范围为1至65535。')
        if args.data_root is not None and not args.data_root.is_absolute():
            raise FundlabError('INVALID_DATA_ROOT', '数据根目录须为绝对路径。')
        config = AppConfig(args.data_root.resolve()) if args.data_root is not None else AppConfig.default()
        if args.schedule_action:
            from fundlab.scheduling.settings import ScheduleSettings
            from fundlab.scheduling.windows_task import WindowsTask
            with runtime_guard(config):
                task = WindowsTask(config)
                if args.schedule_action == 'status':
                    result = task.state(ScheduleSettings(config).read()['update_time'])
                else:
                    result = task.change(args.schedule_action)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.diagnose:
            result = diagnose(config) if args.probe_port is None else diagnose(config, args.probe_port)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print('QFund EXE原型：用户数据目录为 ' + str(config.root), flush=True)
            with runtime_guard(config):
                launch(config, args.port, open_page=not args.no_browser)
    except FundlabError as error:
        print(str(error), file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception:
        print('EXE原型未能完成运行或打包检查，请保留运行信息；原有数据保留。', file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
