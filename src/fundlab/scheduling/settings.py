"""每日统一更新时间；原子保存、修改不触发采集，与自动批次互斥。"""
import json
import os
import re
from uuid import uuid4

from fundlab.collectors.session import ProfileLock
from fundlab.errors import FundlabError
from fundlab.models import utc_now


def validate_time(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
        raise FundlabError("VALIDATION_ERROR", "更新时间须为00:00至23:59之间的北京时间（HH:MM）。")
    return value


class ScheduleSettings:
    def __init__(self, config):
        self.config = config
        self.path = config.data_dir / "schedule-settings.json"

    def read(self):
        if not self.path.exists():
            return {"schema": 1, "update_time": "18:00", "effective_from": None}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if set(data) != {"schema", "update_time", "effective_from"} or data["schema"] != 1:
                raise ValueError()
            validate_time(data["update_time"])
            from fundlab.scheduling.rules import parse_time
            parse_time(data["effective_from"])
            return data
        except (OSError, ValueError, TypeError, KeyError, FundlabError):
            raise FundlabError("SCHEDULE_SETTINGS_INVALID", "自动更新时间设置无法读取，原文件已保留，请检查后重试。") from None

    def save(self, value):
        value = validate_time(value)
        with ProfileLock(self.config.local_dir / "scheduler.lock", FundlabError(
                "SCHEDULE_BUSY", "到期更新正在执行，请等待结束后再修改时间。")):
            previous = self.read()
            if previous["update_time"] == value:
                return previous
            from fundlab.scheduling.windows_task import WindowsTask
            system_task = WindowsTask(self.config)
            task_before = system_task.query()
            task_changed = False
            data = {"schema": 1, "update_time": value, "effective_from": utc_now()}
            temporary = self.path.with_name(self.path.name + "." + uuid4().hex + ".tmp")
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with temporary.open("x", encoding="utf-8") as file:
                    json.dump(data, file, ensure_ascii=False, indent=2)
                    file.flush()
                    os.fsync(file.fileno())
                if task_before['installed']:
                    system_task.register(value, task_before, enabled=task_before['enabled'])
                    task_changed = True
                os.replace(temporary, self.path)
            except OSError:
                if task_changed:
                    try:
                        system_task.call('register', xml=task_before['xml'])
                    except FundlabError:
                        raise FundlabError('SCHEDULE_SYNC_UNCONFIRMED', '设置发布失败，系统任务恢复结果也需核对；请停用任务后刷新检查，勿认为新时间已保存。') from None
                raise FundlabError("SCHEDULE_SETTINGS_WRITE_FAILED", "更新时间未能保存，原设置已保留，请刷新核对后重试。") from None
            finally:
                if temporary.exists():
                    try:
                        temporary.unlink()
                    except OSError:
                        pass
            return data
