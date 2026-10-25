"""交易日、自定义北京时间及合并补跑规则；不启动浏览器。"""
from datetime import datetime, time, timedelta, timezone

from fundlab.scheduling.calendar import load_calendar
from fundlab.scheduling.settings import validate_time

# 本项目现代日期的北京时间固定UTC+8，不依赖Windows另行安装IANA时区库。
BEIJING = timezone(timedelta(hours=8), "Asia/Shanghai")
RETRY_LIMIT = 3
RETRY_DELAY = timedelta(minutes=5)


def beijing(value=None):
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("调度时间必须带时区")
    return value.astimezone(BEIJING)


def parse_time(value):
    return beijing(datetime.fromisoformat(value))


def slot_on(day, update_time="18:00"):
    return datetime.combine(day, time.fromisoformat(validate_time(update_time)), BEIJING)


def next_slot(value, *, inclusive=False, update_time="18:00", calendar=None):
    value = beijing(value)
    calendar = calendar or load_calendar()
    day = value.date()
    while not calendar.is_open(day) or slot_on(day, update_time) < value or (not inclusive and slot_on(day, update_time) == value):
        day += timedelta(days=1)
    return slot_on(day, update_time)


def latest_slot(value, *, update_time="18:00", calendar=None):
    value = beijing(value)
    calendar = calendar or load_calendar()
    day = value.date()
    while not calendar.is_open(day) or slot_on(day, update_time) > value:
        day -= timedelta(days=1)
    return slot_on(day, update_time)


def count_slots(start, end, *, calendar=None):
    calendar = calendar or load_calendar()
    day, finish = start.date(), end.date()
    calendar.require(day)
    calendar.require(finish)
    count = 0
    while day <= finish:
        count += calendar.is_open(day)
        day += timedelta(days=1)
    return count


def build_plan(items, latest, now=None, *, update_time="18:00", calendar=None, effective_from=None):
    now = beijing(now)
    calendar = calendar or load_calendar()
    due = latest_slot(now, update_time=update_time, calendar=calendar)
    following = lambda value, **kw: next_slot(value, update_time=update_time, calendar=calendar, **kw)
    plans = []
    for item in items:
        if not item["enabled"]:
            continue
        # 新加入/重新启用只参加此后的时段，不追补加入名单前或停用期间。
        enrolled_at = max(parse_time(item["added_at"]), parse_time(item["updated_at"]))
        if enrolled_at > due or (effective_from and parse_time(effective_from) > due):
            continue
        enrollment = following(enrolled_at, inclusive=True)
        start = max(enrollment, following(parse_time(effective_from), inclusive=True)) if effective_from else enrollment
        if start > due:
            continue
        previous = latest.get(item["code"])
        carry = {}
        if previous:
            previous_slot = parse_time(previous["slot"])
            if previous_slot > now or previous_slot.date() > due.date():
                continue  # 系统时钟回拨不重新执行未来已认领的时段。
            if previous_slot.date() == due.date():
                if previous["status"] in {"succeeded", "superseded"} or due < start:
                    continue
                if previous["status"] == "skipped":
                    # 当天改为更晚时间并重新加入，可参加新时段；仍保留已有尝试预算和保存阶段。
                    carry = {key: previous[key] for key in ("phase", "attempts", "export_attempts", "run_id", "data_run_id")}
                else:
                    plans.append(dict(previous))
                    continue
            if previous["status"] == "succeeded":
                tomorrow = datetime.combine(previous_slot.date()+timedelta(days=1), time(), BEIJING)
                start = following(max(enrollment, tomorrow), inclusive=True)
            elif previous["status"] not in {"skipped", "superseded"}:
                start = max(enrollment, parse_time(previous["merged_from"]))
        if start > due:
            continue
        plans.append({"slot": due.isoformat(), "code": item["code"], "name": item["name"],
                      "merged_from": start.isoformat(), "merged_count": count_slots(start, due, calendar=calendar),
                      "status": "planned", "phase": "collect", "attempts": 0, "export_attempts": 0,
                      "run_id": None, "data_run_id": None, "error_code": None, "message": "等待执行到期更新。",
                      "retry_at": None, "updated_at": now.isoformat(), "completed_at": None, **carry})
    return plans


def retryable(job, now, *, retry_failed=False):
    if job["status"] in {"succeeded", "skipped", "superseded", "running"}:
        return False
    used = job["export_attempts"] if job["phase"] == "export" else job["attempts"]
    if used >= RETRY_LIMIT:
        return False
    if job["status"] in {"failed", "interrupted"} and not job["retry_at"] and not retry_failed:
        return False
    return retry_failed or not job["retry_at"] or parse_time(job["retry_at"]) <= beijing(now)
