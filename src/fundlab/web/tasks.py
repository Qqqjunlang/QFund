"""本机网页单任务管理：幂等提交、状态持久化与现有业务服务编排。"""
from copy import deepcopy
import json
import os
import re
from threading import Event, RLock, Thread
from uuid import uuid4

from fundlab.errors import FundlabError
from fundlab.models import utc_now
from fundlab.services.login import login_session
from fundlab.services.search import search_candidates
from fundlab.services.update import retry_export, update_one
from fundlab.validation import validate_code, validate_query

ACTIVE = {"queued", "running", "waiting_login"}
STAGES = {"queued": "任务已提交", "searching": "正在读取基金列表", "fetching": "正在打开专用 Edge，读取真实行情",
          "parsing": "正在核对行情字段与单位", "saving": "正在保存本地累计数据", "exporting": "正在导出 Excel 和 CSV",
          "opening_login": "正在打开专用 Edge 登录窗口", "waiting_login": "请在专用 Edge 中本人登录，验证后窗口会自动关闭"}


class TaskManager:
    def __init__(self, config):
        self.config = config
        self.path = config.local_dir / "web-tasks.json"
        self.lock = RLock()
        self.jobs = []
        self.stops = {}
        self.persistence_error = False
        if self.path.is_file():
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(saved, list):
                    raise ValueError()
                self.jobs = saved[-50:]
                for job in self.jobs:
                    if not isinstance(job, dict) or not all(k in job for k in ("id", "kind", "status", "request_id", "payload")):
                        raise ValueError()
                    if job["status"] in ACTIVE:
                        job.update(status="interrupted", message="服务已重启，原任务最终状态未确认。请先查看本地数据；不会自动重复采集。",
                                   completed_at=utc_now())
                self._save()
            except (OSError, ValueError, TypeError, KeyError):
                raise FundlabError("TASK_RECORD_READ_FAILED", "任务记录无法读取，请保留 .local/web-tasks.json 并检查；未启动新采集。") from None

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.jobs, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def _persist(self):
        try:
            self._save()
        except OSError:
            self.persistence_error = True

    def _public(self, job):
        if job is None:
            return None
        value = deepcopy(job)
        if self.persistence_error:
            value["log_warning"] = "任务记录写入失败，当前结果仍可查看，请检查磁盘；不要重复提交采集。"
        return value

    def state(self):
        with self.lock:
            active = next((j for j in reversed(self.jobs) if j["status"] in ACTIVE), None)
            return {"active": self._public(active), "latest": self._public(self.jobs[-1]) if self.jobs else None}

    def get(self, job_id):
        with self.lock:
            job = next((j for j in self.jobs if j["id"] == job_id), None)
            if job is None:
                raise FundlabError("TASK_NOT_FOUND", "任务不存在或已不在近期记录中。")
            return self._public(job)

    def submit(self, kind, payload, request_id):
        if kind not in {"search", "update", "login", "export"}:
            raise FundlabError("VALIDATION_ERROR", "任务类型不支持。")
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", request_id):
            raise FundlabError("VALIDATION_ERROR", "请求标识不正确，请刷新页面重试。")
        if not isinstance(payload, dict) or any(not isinstance(value, str) for value in payload.values()):
            raise FundlabError("VALIDATION_ERROR", "任务参数须为文本。")
        if kind == "search":
            payload = {"query": validate_query(payload.get("query", ""))}
            if payload["query"].isdecimal():
                validate_code(payload["query"])
        elif kind in {"update", "export"}:
            payload = {"code": validate_code(payload.get("code", ""))}
        else:
            payload = {}
        with self.lock:
            previous = next((j for j in self.jobs if j["request_id"] == request_id), None)
            if previous:
                if previous["kind"] != kind or previous["payload"] != payload:
                    raise FundlabError("REQUEST_CONFLICT", "同一请求标识不能用于另一项操作。")
                return self._public(previous)
            if any(j["status"] in ACTIVE for j in self.jobs):
                raise FundlabError("TASK_BUSY", "已有任务进行中，请等待结束后再操作。")
            job = {"id": str(uuid4()), "request_id": request_id, "kind": kind, "payload": payload,
                   "status": "queued", "stage": "queued", "message": STAGES["queued"], "started_at": utc_now(),
                   "completed_at": None, "result": None, "error_code": None}
            self.jobs.append(job)
            self.jobs = self.jobs[-50:]
            try:
                self._save()
            except OSError:
                self.jobs.remove(job)
                raise FundlabError("TASK_RECORD_WRITE_FAILED", "无法保存任务记录，本次未启动；请检查磁盘后重试。") from None
            self.stops[job["id"]] = Event()
            Thread(target=self._run, args=(job,), daemon=True, name="fundlab-web-task").start()
            return self._public(job)

    def _progress(self, job, stage):
        with self.lock:
            job.update(status="waiting_login" if stage == "waiting_login" else "running", stage=stage,
                       message=STAGES.get(stage, "正在处理任务"))
            self._persist()

    def _run(self, job):
        try:
            kind, payload = job["kind"], job["payload"]
            progress = lambda stage: self._progress(job, stage)
            if kind == "search":
                progress("searching")
                result = {"status": "succeeded", **search_candidates(self.config, payload["query"])}
            elif kind == "login":
                progress("opening_login")
                result = login_session(self.config, progress=progress, stop_event=self.stops[job["id"]])
            else:
                if kind == "update":
                    progress("fetching")
                    outcome = update_one(self.config, payload["code"], progress=progress)
                else:
                    progress("exporting")
                    outcome = retry_export(self.config, payload["code"])
                raw = outcome.to_dict()
                result = {k: raw[k] for k in ("run_id", "code", "name", "status", "inserted", "updated", "unchanged", "total", "message", "error_code")}
                result["source_start"] = raw["coverage"].get("source_start")
                result["source_end"] = raw["coverage"].get("source_end")
                if result.get("error_code") == "AUTH_REQUIRED":
                    result["message"] = "登录已失效，请点击“专用 Edge 登录”，由本人登录后重新获取。"
            with self.lock:
                job.update(status=result.get("status", "succeeded"), result=result,
                           message=result.get("message", "任务完成。"), error_code=result.get("error_code"))
        except FundlabError as error:
            with self.lock:
                job.update(status="cancelled" if error.code == "LOGIN_CANCELLED" else "failed",
                           error_code=error.code, message=("登录已失效，请点击“专用 Edge 登录”，由本人登录后重试。" if error.code == "AUTH_REQUIRED" else str(error)))
        except Exception:
            with self.lock:
                job.update(status="failed", error_code="TASK_ERROR", message="任务未完成，请检查网络、专用 Edge 与本地权限后重试；已有数据保留。")
        finally:
            with self.lock:
                job["completed_at"] = utc_now()
                self.stops.pop(job["id"], None)
                self._persist()

    def cancel_login(self, job_id):
        with self.lock:
            job = next((j for j in self.jobs if j["id"] == job_id), None)
            if job is None:
                raise FundlabError("TASK_NOT_FOUND", "任务不存在。")
            if job["kind"] != "login":
                raise FundlabError("VALIDATION_ERROR", "采集保存过程中不支持强制取消；请等待任务结束。")
            if job["status"] in ACTIVE and job_id in self.stops:
                self.stops[job_id].set()
                job["message"] = "正在关闭专用登录窗口…"
                self._persist()
            return self._public(job)
