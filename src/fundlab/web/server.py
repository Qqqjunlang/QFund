"""仅监听回环地址的本地工作台：查询、下载、分析与手动采集。"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import secrets
import sys
from urllib.parse import parse_qs, urlsplit

from fundlab.config import AppConfig
from fundlab.collectors.session import ProfileLock
from fundlab.errors import FundlabError
from fundlab.presentation import APP_NAME, typography_css
from fundlab.services.data_query import download_history, history_page, overview
from fundlab.services.analysis import price_summary
from fundlab.services.watchlist import WatchlistService
from fundlab.scheduling.runner import schedule_status
from fundlab.scheduling.settings import ScheduleSettings
from fundlab.web.premium import chart_document, chart_policy, error_document
from fundlab.web.tasks import TaskManager
from fundlab.web.identity import service_identity
from fundlab.web.management import ManagementRoutes, POST_FIELDS, PREVIEW_PATH, PREVIEW_BODY_LIMIT

ASSETS = Path(__file__).with_name("static")
CHART_PARAMS = {"/analysis/premium": {"code"}, "/analysis/price": {"code", "periods", "start", "end"}}
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8"),
          "/qfund-icon.svg": ("qfund-icon.svg", "image/svg+xml"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/management.js": ("management.js", "text/javascript; charset=utf-8"),
          "/browser_compat.js": ("browser_compat.js", "text/javascript; charset=utf-8")}


class LocalHTTPServer(ThreadingHTTPServer):
    # Windows的SO_REUSEADDR可能让两个版本同时监听同一端口。
    allow_reuse_address = False

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def server_close(self):
        try:
            super().server_close()
        finally:
            lock = getattr(self, "instance_lock", None)
            if lock is not None:
                lock.__exit__(None, None, None)
                self.instance_lock = None


def make_server(config: AppConfig, port: int = 8765) -> ThreadingHTTPServer:
    csrf_token = secrets.token_urlsafe(32)
    tasks = None
    watchlist = WatchlistService(config)
    management = ManagementRoutes(config)

    class Handler(BaseHTTPRequestHandler):
        server_version = "FundlabLocal"

        def reply(self, status, content, mime, filename=None, policy=None):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", policy or "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(content)

        def json_reply(self, status, value):
            self.reply(status, json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"), "application/json; charset=utf-8")

        def chart_reply(self, status, document):
            self.reply(status, document.encode("utf-8"), "text/html; charset=utf-8", policy=chart_policy(document))

        def local_host(self):
            return self.headers.get("Host") in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

        def do_GET(self):
            if not self.local_host():
                self.json_reply(403, {"message": "请通过本机工作台地址访问。"})
                return
            try:
                url = urlsplit(self.path)
                if url.path in STATIC:
                    name, mime = STATIC[url.path]
                    content = (ASSETS / name).read_bytes()
                    if name == "style.css":
                        content = typography_css().encode("utf-8") + content
                    self.reply(200, content, mime)
                    return
                if url.path == "/api/health":
                    self.json_reply(200, {**service_identity(config), "status": "ready", "pid": os.getpid()})
                    return
                if url.path == "/api/overview":
                    self.json_reply(200, overview(config))
                    return
                if url.path == "/api/session":
                    self.json_reply(200, {"csrf_token": csrf_token})
                    return
                if url.path == "/api/watchlist":
                    if url.query:
                        raise FundlabError("VALIDATION_ERROR", "名单查询不接受额外参数。")
                    self.json_reply(200, watchlist.state())
                    return
                if url.path == "/api/schedule":
                    if url.query:
                        raise FundlabError("VALIDATION_ERROR", "执行计划查询不接受额外参数。")
                    self.json_reply(200, schedule_status(config))
                    return
                if url.path == "/api/tasks":
                    self.json_reply(200, tasks.state())
                    return
                params = parse_qs(url.query, keep_blank_values=True, max_num_fields=10)
                managed = management.get(url.path, params)
                if managed is not None:
                    self.json_reply(200, managed)
                    return
                get = lambda key: params.get(key, [""])[0]
                if url.path == '/api/price-summary':
                    if set(params) - {'code', 'start', 'end', 'price'} or any(len(v) != 1 for v in params.values()):
                        raise FundlabError('VALIDATION_ERROR', '查询参数不正确。')
                    self.json_reply(200, price_summary(config, get('code'), start=get('start'), end=get('end'),
                                                       comparison_price=get('price') if 'price' in params else None))
                    return
                allowed = CHART_PARAMS.get(url.path, {"id"} if url.path == "/api/task" else
                           {"code", "start", "end", "page"} if url.path == "/api/history" else {"code", "start", "end", "format"})
                if set(params) - allowed or any(len(v) != 1 for v in params.values()):
                    raise FundlabError("VALIDATION_ERROR", "查询参数不正确。")
                if url.path == "/api/task":
                    self.json_reply(200, tasks.get(get("id")))
                elif url.path in CHART_PARAMS:
                    kind = url.path.rsplit("/", 1)[1]
                    options = {key: get(key) for key in CHART_PARAMS[url.path] - {"code"}}
                    self.chart_reply(200, chart_document(config, get("code"), kind=kind, **options))
                elif url.path == "/api/history":
                    result = history_page(config, get("code"), get("start"), get("end"), int(get("page") or "1"))
                    self.json_reply(200, result)
                elif url.path == "/api/download":
                    format = get("format")
                    name, content = download_history(config, get("code"), format, get("start"), get("end"))
                    mime = "text/csv; charset=utf-8" if format == "csv" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    self.reply(200, content, mime, name)
                else:
                    self.json_reply(404, {"message": "页面或文件不存在。"})
            except FundlabError as error:
                status = 404 if error.code in {"NO_DATA", "TASK_NOT_FOUND"} else 409 if error.code == "FUND_TRASHED" else 400 if error.code == "VALIDATION_ERROR" else 500
                if url.path in CHART_PARAMS:
                    self.chart_reply(status, error_document(get("code"), str(error), kind=url.path.rsplit("/", 1)[1]))
                else:
                    self.json_reply(status, {"code": error.code, "message": str(error)})
            except ValueError:
                self.json_reply(400, {"message": "查询参数或页码不正确。"})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                message = "本地服务暂时无法完成请求，请重试；原有数据仍保留。"
                if url.path in CHART_PARAMS:
                    self.chart_reply(500, error_document(get("code"), message, kind=url.path.rsplit("/", 1)[1]))
                else:
                    self.json_reply(500, {"message": message})

        def do_POST(self):
            origins = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
            token = self.headers.get("X-Fundlab-CSRF", "")
            if (not self.local_host() or (self.headers.get("Origin") is not None and self.headers.get("Origin") not in origins)
                    or not token.isascii() or not secrets.compare_digest(token, csrf_token)):
                self.json_reply(403, {"message": "请求未通过本机校验，请刷新工作台后重试。"})
                return
            if self.headers.get_content_type() != "application/json" or self.headers.get("Transfer-Encoding"):
                self.json_reply(400, {"message": "请求须使用JSON格式。"})
                return
            try:
                url = urlsplit(self.path)
                if url.query:
                    raise ValueError()
                length = int(self.headers.get("Content-Length", "0"))
                limit = PREVIEW_BODY_LIMIT if url.path == PREVIEW_PATH else 262144 if url.path == "/api/groups/change" else 4096
                if length > limit:
                    self.json_reply(413, {"message": "提交内容超过允许大小。"})
                    return
                if length <= 0:
                    raise ValueError()
                self.connection.settimeout(10)
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError()
                if url.path in POST_FIELDS:
                    self.json_reply(200, management.post(url.path, data))
                    return
                if url.path == "/api/schedule/settings":
                    if set(data) != {"update_time"}:
                        raise ValueError()
                    ScheduleSettings(config).save(data["update_time"])
                    self.json_reply(200, schedule_status(config))
                    return
                if url.path in {'/api/schedule/install', '/api/schedule/disable', '/api/schedule/remove'}:
                    if data:
                        raise ValueError()
                    from fundlab.scheduling.windows_task import WindowsTask
                    WindowsTask(config).change(url.path.rsplit('/', 1)[1])
                    self.json_reply(200, schedule_status(config))
                    return
                if url.path in {"/api/watchlist/add", "/api/watchlist/remove", "/api/watchlist/set-enabled"}:
                    action = url.path.rsplit("/", 1)[1].replace("-", "_")
                    keys = {"code", "enabled"} if action == "set_enabled" else {"code"}
                    if set(data) != keys:
                        raise ValueError()
                    self.json_reply(200, watchlist.change(action, data["code"], data.get("enabled")))
                    return
                if url.path == "/api/tasks/cancel":
                    if set(data) != {"job_id"} or not isinstance(data["job_id"], str):
                        raise ValueError()
                    self.json_reply(200, tasks.cancel_login(data["job_id"]))
                    return
                actions = {"/api/tasks/search": ("search", {"query"}), "/api/tasks/update": ("update", {"code"}),
                           "/api/tasks/login": ("login", set()), "/api/tasks/export": ("export", {"code"})}
                if url.path not in actions:
                    self.json_reply(404, {"message": "操作不存在。"})
                    return
                kind, keys = actions[url.path]
                required = keys | {"request_id"}
                optional = {"restore_from_trash"} if kind == "update" else set()
                if not required <= data.keys() <= required | optional:
                    raise ValueError()
                payload = {key: data[key] for key in keys}
                if "restore_from_trash" in data:
                    payload["restore_from_trash"] = data["restore_from_trash"]
                job = tasks.submit(kind, payload, data["request_id"])
                self.json_reply(202, job)
            except FundlabError as error:
                if error.code in {"TASK_BUSY", "REQUEST_CONFLICT", "SCHEDULE_BUSY", "UPDATE_BUSY",
                                  "WATCHLIST_BUSY", "DATA_STALE", "PREVIEW_STALE", "FUND_TRASHED",
                                  "FUND_STATE_CONFLICT", "GROUP_EXISTS", "DATE_EXISTS", "DATE_DELETED"}:
                    status = 409
                elif error.code in {"NO_DATA", "NO_GROUP", "TASK_NOT_FOUND"}:
                    status = 404
                elif error.code in {"VALIDATION_ERROR", "IMPORT_OPTIONS", "IMPORT_FILE_SIZE", "IMPORT_TYPE", "IMPORT_READ_FAILED"}:
                    status = 400
                else:
                    status = 500
                self.json_reply(status, {"code": error.code, "message": str(error)})
            except (ValueError, TypeError, UnicodeDecodeError):
                self.json_reply(400, {"message": "提交参数不正确，请检查输入后重试。"})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                self.json_reply(500, {"message": "未能确认提交结果，请刷新任务状态；不要立即重复采集。"})

        def log_message(self, format, *args):
            pass

    server = LocalHTTPServer(("127.0.0.1", port), Handler)
    try:
        server.instance_lock = ProfileLock(config.local_dir / "web-server.lock", busy_error=FundlabError(
            "SERVER_BUSY", "本项目已有工作台服务运行。请使用原工作台页面；如需更换端口，请等待任务完成并关闭原运行窗口后再启动。"))
        server.instance_lock.__enter__()
        tasks = TaskManager(config)
    except Exception:
        server.server_close()
        raise
    server.task_manager = tasks
    return server


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=f"{APP_NAME} 基金数据工作台")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        with make_server(AppConfig.default(), args.port) as server:
            print(f"{APP_NAME} 工作台：http://127.0.0.1:{server.server_port} （按 Ctrl+C 关闭）", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    except FundlabError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except OSError:
        print("工作台启动失败，端口可能已占用；可用 --port 指定其他端口。", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
