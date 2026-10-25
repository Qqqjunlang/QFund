"""双击入口：检查环境，识别同项目服务，准备就绪后打开浏览器。"""
import argparse
import importlib
import json
import subprocess
import sys
from threading import Event, Thread
import time
from urllib.error import URLError
from urllib.request import build_opener, ProxyHandler, HTTPRedirectHandler
import webbrowser

from fundlab.config import AppConfig
from fundlab.errors import FundlabError
from fundlab.presentation import APP_NAME
from fundlab.web.identity import service_identity


def check_environment():
    if sys.version_info < (3, 11):
        raise FundlabError("PYTHON_VERSION", "项目需要 Python 3.11 或更新版本，请按 README 重建项目环境。")
    missing = []
    for module in ("playwright.sync_api", "openpyxl", "plotly.graph_objects"):
        try:
            importlib.import_module(module)
        except (ImportError, OSError):
            missing.append(module.split('.')[0])
    if missing:
        raise FundlabError("DEPENDENCY_MISSING", "项目依赖未就绪："+'、'.join(missing)+"。请按 README 的重建环境步骤修复，不要使用系统默认 Python。")


def existing_service(config, port):
    """只读识别；绕过系统代理访问回环地址，不跟随重定向打开外站。"""
    endpoint = f"http://127.0.0.1:{port}/api/health"
    try:
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        opener = build_opener(ProxyHandler({}), NoRedirect())
        with opener.open(endpoint, timeout=.6) as response:
            if response.geturl() != endpoint or response.status != 200:
                return None
            value = json.loads(response.read(4097))
        if (isinstance(value, dict) and all(value.get(k) == v for k, v in service_identity(config).items())
                and value.get("status") == "ready" and type(value.get("pid")) is int and value["pid"] > 0):
            return value
    except (URLError, OSError, ValueError, TypeError):
        pass
    return None


def open_workbench(url):
    """打开普通Edge工作台页；采集仍使用原专用登录窗口。"""
    from fundlab.collectors.session import find_edge
    edge = find_edge()
    if edge:
        try:
            subprocess.Popen([str(edge), "--new-tab", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError:
            pass
    try:
        return bool(webbrowser.open(url))
    except (OSError, webbrowser.Error):
        return False


def launch(config, port=8765, *, open_page=True, startup_timeout=6):
    restart_entry = "QFund.exe" if getattr(sys, 'frozen', False) else "启动工作台.cmd"
    if not 1 <= port <= 65535:
        raise FundlabError("INVALID_PORT", "端口必须是1至65535之间的整数。")
    check_environment()
    url = f"http://127.0.0.1:{port}/"
    existing = existing_service(config, port)
    if existing:
        print("工作台已经运行，"+("正在打开现有页面。" if open_page else "复用现有服务。"), flush=True)
        if open_page and not open_workbench(url):
            print("浏览器未能自动打开，请手动访问："+url, flush=True)
        print(f"{APP_NAME} 工作台地址："+url, flush=True)
        return "reused"

    # 两个启动器可能同时到达此处。只有成功绑定端口者才初始化任务管理。
    from fundlab.web.server import make_server
    try:
        server = make_server(config, port)
    except OSError as error:
        if error.errno not in {48, 98, 10048, 10013} and getattr(error, "winerror", None) not in {10048, 10013}:
            raise FundlabError("START_FAILED", "工作台启动失败，请检查本地端口权限和项目目录读写权限；不会修改已有数据。") from None
        deadline = time.monotonic()+startup_timeout
        while time.monotonic() < deadline:
            existing = existing_service(config, port)
            if existing:
                print("另一启动窗口已准备好工作台，"+("正在打开现有页面。" if open_page else "复用现有服务。"), flush=True)
                if open_page and not open_workbench(url):
                    print("浏览器未能自动打开，请手动访问："+url, flush=True)
                print(f"{APP_NAME} 工作台地址："+url, flush=True)
                return "reused"
            time.sleep(.15)
        raise FundlabError("PORT_IN_USE", f"端口{port}已被占用或不允许监听，未识别到本项目的可用工作台。请先关闭以前的工作台运行窗口后重试；若由其他程序占用，可按 README 指定其他端口。不要关闭正在采集的窗口。") from None

    ready = Event()
    worker = Thread(target=server.serve_forever, kwargs={"poll_interval": .1}, daemon=True)
    with server:
        worker.start()
        try:
            deadline = time.monotonic()+startup_timeout
            while time.monotonic() < deadline:
                if existing_service(config, port):
                    ready.set()
                    break
                if not worker.is_alive():
                    break
                time.sleep(.1)
            if not ready.is_set():
                raise FundlabError("START_TIMEOUT", "工作台未能完成就绪检查，请重试；不要反复点击。")
            print(f"{APP_NAME} 已启动。", flush=True)
            print(f"{APP_NAME} 工作台地址："+url, flush=True)
            print("请保留本运行窗口。关闭网页后可再次双击打开；关闭此窗口会停止服务。", flush=True)
            print("结束使用前，请等待采集或导出任务完成，再按 Ctrl+C 停止。", flush=True)
            if open_page and not open_workbench(url):
                print("浏览器未能自动打开，请手动访问："+url, flush=True)
            while worker.is_alive():
                try:
                    worker.join(.4)
                except KeyboardInterrupt:
                    if server.task_manager.state()["active"]:
                        print("\n还有任务进行中，请等待结束后再按 Ctrl+C；等待本人登录时，可先在网页取消登录。", flush=True)
                    else:
                        print(f"\n正在关闭工作台。下次双击“{restart_entry}”可重新打开。", flush=True)
                        break
            else:
                raise FundlabError("SERVER_STOPPED", "工作台服务意外停止，请检查环境后重新双击启动；已保存的数据保留。")
        except KeyboardInterrupt:
            print(f"\n正在关闭工作台。下次双击“{restart_entry}”可重新打开。", flush=True)
        finally:
            server.shutdown()
            worker.join(3)
    return "stopped"


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=f"启动 {APP_NAME} 本地基金数据工作台")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="仅启动服务，不自动打开浏览器")
    args = parser.parse_args()
    try:
        launch(AppConfig.default(), args.port, open_page=not args.no_browser)
    except FundlabError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception:
        print("启动未完成，请检查项目环境是否完整，按 README 修复后重试；原有数据保留。", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
