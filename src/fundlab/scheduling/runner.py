"""显式调用一次到期执行；不启动计时线程、不注册Windows任务。"""
from uuid import uuid4

from fundlab.collectors.session import ProfileLock
from fundlab.errors import FundlabError
from fundlab.scheduling.rules import RETRY_DELAY, RETRY_LIMIT, beijing, build_plan, latest_slot, next_slot, parse_time, retryable
from fundlab.scheduling.store import ScheduleStore, market_run
from fundlab.scheduling.calendar import load_calendar
from fundlab.scheduling.settings import ScheduleSettings
from fundlab.services.update import retry_export, update_one
from fundlab.services.watchlist import WatchlistService

TRANSIENT = {"NETWORK_ERROR", "SOURCE_TIMEOUT", "RATE_LIMITED", "BROWSER_START_FAILED"}
BUSY = {"UPDATE_BUSY", "SESSION_BUSY"}


def schedule_status(config, *, now=None, items=None):
    now = beijing(now)
    if items is None:
        items = WatchlistService(config).state()["items"]
    saved = ScheduleStore(config).read()
    settings = ScheduleSettings(config).read()
    result = {"engine_ready": True, "scheduler_enabled": False, "timezone": "Asia/Shanghai",
              "checked_at": now.isoformat(), "update_time": settings["update_time"], "effective_from": settings["effective_from"],
              "next_slot": None, "latest_slot": None, "pending": [], "pending_count": 0,
              "retry_limit": RETRY_LIMIT, "recent": saved["recent"], "last_check": saved["check"],
              "calendar": None, "calendar_ready": False, "calendar_error": None, "calendar_warning": None,
              "run_allowed": False, "message": "执行规则已就绪。"}
    try:
        calendar = load_calendar()
        result["calendar"] = calendar.public()
        calendar.require(now.date())
        args = {"update_time": settings["update_time"], "calendar": calendar}
        # 年末下一时段尚无可靠日历时，仍可显示/处理本年度已确认时段。
        try:
            result["next_slot"] = next_slot(now, **args).isoformat()
        except FundlabError as error:
            result["calendar_warning"] = str(error)
        result["latest_slot"] = latest_slot(now, **args).isoformat()
        planned = build_plan(items, saved["latest"], now, effective_from=settings["effective_from"], **args)
        result.update(calendar_ready=True, pending=planned, pending_count=len(planned),
                      run_allowed=calendar.is_open(now.date()) and now.strftime("%H:%M") >= settings["update_time"])
        if not calendar.is_open(now.date()):
            result["message"] = "今天为沪深市场休市日，到期更新留待后续交易日处理。"
        elif not result["run_allowed"]:
            result["message"] = "尚未到当天设定时间，到期更新暂不执行。"
    except FundlabError as error:
        if not error.code.startswith("CALENDAR_"):
            raise
        result.update(calendar_error=str(error), message=str(error))
    from fundlab.scheduling.windows_task import system_task_status
    result['system_task'] = system_task_status(config, settings['update_time'])
    result['scheduler_enabled'] = bool(result['system_task']['enabled'] and result['system_task']['healthy'])
    return result


def _finish(job, raw, now):
    job.update(updated_at=now.isoformat(), error_code=raw.get("error_code"),
               message=raw.get("message") or "本次到期执行未完成。", retry_at=None, completed_at=None)
    error = raw.get("error_code")
    if raw["status"] == "succeeded":
        if job["phase"] == "collect":
            job["data_run_id"] = job["run_id"]
        job.update(status="succeeded", completed_at=now.isoformat())
    elif raw.get("data_saved") or raw["status"] in {"export_failed", "saved_pending_export"}:
        if job["phase"] == "collect":
            job.update(data_run_id=job["run_id"], export_attempts=1)
        job.update(status="failed", phase="export", message="行情已保存，待单独重试导出。")
        if error != "EXPORT_RECOVERY_REQUIRED":
            job["retry_at"] = (now + RETRY_DELAY).isoformat()
    elif error in BUSY:
        counter = "export_attempts" if job["phase"] == "export" else "attempts"
        job[counter] = max(0, job[counter] - 1)
        job.update(status="planned", retry_at=(now + RETRY_DELAY).isoformat(), message="手动更新、导出或专用Edge正在使用，已延后，稍后可补跑。")
    else:
        job["status"] = "interrupted" if raw["status"] in {"interrupted", "running"} else "failed"
        if error in TRANSIENT or job["status"] == "interrupted":
            job["retry_at"] = (now + RETRY_DELAY).isoformat()
        if error == "AUTH_REQUIRED":
            job["message"] = "登录已失效，请先在工作台由本人登录，再显式重试；本时段不会自动反复打开登录窗口。"
    counter = "export_attempts" if job["phase"] == "export" else "attempts"
    if job["status"] != "succeeded" and job[counter] >= RETRY_LIMIT:
        job.update(retry_at=None, message=job["message"] + " 本时段已达到3次尝试上限。")


def _recover(config, store, jobs, now):
    for job in jobs:
        if job["status"] != "running":
            continue
        raw = market_run(config, job["run_id"])
        if raw and (raw["code"] != job["code"] or (raw["mode"] == "export") != (job["phase"] == "export")):
            raise FundlabError("SCHEDULE_RUN_MISMATCH", "原自动执行与基金代码不一致，请保留记录核对，未重新采集。")
        if raw is None:
            job.update(status="interrupted", error_code="INTERRUPTED", message="上次认领后尚未启动行情运行，可重新执行。",
                       retry_at=now.isoformat(), updated_at=now.isoformat())
            counter = "export_attempts" if job["phase"] == "export" else "attempts"
            if job[counter] >= RETRY_LIMIT:
                job.update(retry_at=None, message="上次认领后尚未启动行情运行，本时段已达到3次执行尝试上限，请先检查环境。")
        else:
            _finish(job, raw, now)
        store.save(job)


def run_due_updates(config, *, now=None, retry_failed=False):
    """一次调用每只到期基金至多尝试一次；后续调用按持久退避/上限继续。"""
    fixed = now is not None
    now = beijing(now)
    clock = (lambda: now) if fixed else beijing
    try:
        with ProfileLock(config.local_dir / "scheduler.lock", FundlabError("SCHEDULE_BUSY", "自动更新正在执行，未重复启动。")):
            return _run_locked(config, now, retry_failed, clock)
    except FundlabError as error:
        if error.code == "SCHEDULE_BUSY":
            return {"status": "deferred", "message": str(error), "results": []}
        raise


def _run_locked(config, now, retry_failed, clock):
    store = ScheduleStore(config)
    settings = ScheduleSettings(config).read()
    calendar = load_calendar()
    calendar.require(now.date())
    saved = store.read()
    # 此锁排除了其他自动执行进程；只有此时才能核对前次未完成记录。
    _recover(config, store, list(saved["latest"].values()), now)
    if not calendar.is_open(now.date()):
        message = "今天为沪深市场休市日，本次跳过，未执行的计划留待后续交易日合并补跑。"
        store.checked(now.isoformat(), "skipped_closed", message)
        return {"status": "skipped_closed", "message": message, "results": []}
    if now.strftime("%H:%M") < settings["update_time"]:
        message = "尚未到当天设定的北京时间，本次等待，不提前执行或补跑。"
        store.checked(now.isoformat(), "not_due", message)
        return {"status": "not_due", "message": message, "results": []}
    items = WatchlistService(config).state()["items"]
    planned = build_plan(items, store.read()["latest"], now, update_time=settings["update_time"],
                         calendar=calendar, effective_from=settings["effective_from"])
    if not any(item["enabled"] for item in items):
        store.checked(now.isoformat(), "skipped_empty", "名单为空或全部停用，本次跳过。")
        return {"status": "skipped_empty", "message": "名单为空或全部停用，本次跳过。", "results": []}
    if not planned:
        store.checked(now.isoformat(), "not_due", "没有需要执行的到期时段，未重复采集。")
        return {"status": "not_due", "message": "没有需要执行的到期时段，未重复采集。", "results": []}
    store.plan(planned)
    latest = store.read()["latest"]
    results = []
    auth_blocked = False
    for plan in planned:
        job = dict(latest[plan["code"]])
        # 名单可能在本批次运行时被用户修改；只影响尚未开始的基金。
        current = next((item for item in WatchlistService(config).state()["items"] if item["code"] == job["code"]), None)
        if (not current or not current["enabled"] or
                next_slot(max(parse_time(current["added_at"]), parse_time(current["updated_at"])), inclusive=True,
                          update_time=settings["update_time"], calendar=calendar).date() > parse_time(job["slot"]).date()):
            job.update(status="skipped", message="已移出或停用，本次未开始执行。", updated_at=now.isoformat())
            store.save(job)
            results.append(job)
            continue
        if not retryable(job, clock(), retry_failed=retry_failed):
            results.append(job)
            continue
        if auth_blocked:
            job.update(status="failed", error_code="AUTH_REQUIRED", retry_at=None,
                       message="专用Edge登录已失效，等待本人登录后显式重试。", updated_at=now.isoformat())
            store.save(job)
            results.append(job)
            continue
        if not current["available"]:
            job.update(status="failed", error_code="NO_DATA", retry_at=None,
                       message="本地行情不可用，请先恢复数据后显式重试。", updated_at=now.isoformat())
            store.save(job)
            results.append(job)
            continue
        counter = "export_attempts" if job["phase"] == "export" else "attempts"
        job.update(status="running", run_id=str(uuid4()), updated_at=clock().isoformat(), completed_at=None)
        job[counter] += 1
        store.save(job)  # 此处失败时不执行采集或导出。
        try:
            worker = retry_export if job["phase"] == "export" else update_one
            outcome = worker(config, job["code"], trigger="scheduled:" + job["slot"], run_id=job["run_id"])
            raw = outcome.to_dict()
        except FundlabError as error:
            if error.code in BUSY:
                raw = {"status": "failed", "error_code": error.code, "message": str(error)}
            else:
                # 可能已提交行情：保留running和run_id供下次只读核对，不盲目重试。
                raise FundlabError("SCHEDULE_RESULT_UNCONFIRMED", "不能确认本次执行结果，请先核对记录；下次会按原运行编号恢复。") from None
        except KeyboardInterrupt:
            raise  # 保留认领；下次检查是否已保存，再决定采集或仅导出。
        except Exception:
            raise FundlabError("SCHEDULE_RESULT_UNCONFIRMED", "执行意外中断，原认领已保留，下次会先核对行情运行，不盲目重采。") from None
        _finish(job, raw, clock())
        store.save(job)
        results.append(job)
        auth_blocked = job["error_code"] == "AUTH_REQUIRED"
        if job["error_code"] in BUSY:
            break  # 不对剩余基金连续争抢手动任务/Edge锁，待后续调用补跑。
    statuses = {item["status"] for item in results}
    status = "succeeded" if statuses <= {"succeeded", "skipped"} else "pending"
    message = "本次到期基金已处理。" if status == "succeeded" else "部分基金待重试或等待本人处理，请查看名单中的执行结果。"
    store.checked(clock().isoformat(), status, message)
    return {"status": status, "message": message, "results": results}
