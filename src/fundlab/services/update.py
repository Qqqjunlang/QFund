"""统一手动更新编排：真实窗口→严格解析→事务提交→导出→记录结果。"""
from uuid import uuid4
import sqlite3
from typing import Callable

from fundlab.collectors.jisilu_qdii import fetch_window
from fundlab.collectors.session import ProfileLock
from fundlab.config import AppConfig
from fundlab.errors import FundlabError
from fundlab.exports.service import export_history
from fundlab.models import UpdateResult
from fundlab.parsers.history import parse_history
from fundlab.storage.repository import Repository
from fundlab.validation import validate_code


def update_lock(config: AppConfig):
    return ProfileLock(config.local_dir / "update.lock", FundlabError("UPDATE_BUSY", "已有更新或导出任务进行中，请等待结束后重试。"))


def _export(repo: Repository, config: AppConfig, result: UpdateResult):
    fund = repo.get_fund(result.code)
    if fund is None:
        raise FundlabError("NO_DATA", "本地没有该基金，请先更新数据。")
    rows = repo.query_history(result.code)
    result.name, result.total = fund.name, len(rows)
    result.data_saved = True
    try:
        result.exports = export_history(config, fund, rows, result.coverage, result.run_id)
        result.status = "succeeded"
        result.message = "数据已保存，Excel、CSV 和来源说明已导出。"
    except FundlabError as error:
        result.status, result.error_code = "export_failed", error.code
        result.message = str(error)
    except Exception:
        result.status, result.error_code = "export_failed", "EXPORT_ERROR"
        result.message = "本地数据已保存；导出失败，可单独运行 export 重试，无需重新采集。"


def update_one(config: AppConfig, code: str, *, mode: str = "incremental",
               progress: Callable[[str], None] | None = None,
               trigger: str = "manual", run_id: str | None = None) -> UpdateResult:
    def notify(stage):
        if progress is not None:
            try:
                progress(stage)
            except Exception:
                pass  # 页面状态通知失败不得改变数据事务。
    code = validate_code(code)
    if mode not in {"initial", "incremental", "full"}:
        raise FundlabError("VALIDATION_ERROR", "更新模式不受支持。")
    with update_lock(config):
        repo = Repository(config.database_path)
        result = UpdateResult(run_id or str(uuid4()), code)
        actual_mode = "initial" if repo.get_fund(code) is None else mode
        repo.start_run(result.run_id, code, actual_mode, trigger)
        saved = False
        try:
            print(f"正在读取 {code} 的真实 QDII 历史窗口……", flush=True)
            notify("fetching")
            window = fetch_window(config, code)
            if window.fund.code != code or window.fund.source != "jisilu":
                raise FundlabError("SCHEMA_CHANGED", "来源基金与请求不一致，本次未保存。")
            notify("parsing")
            records = parse_history(window.rows, code, window.fetched_at)
            result.name = window.fund.name
            result.coverage = {**window.coverage, "source_start": records[0].price_date,
                               "source_end": records[-1].price_date, "fetched_at": window.fetched_at,
                               "source_rows": len(records)}
            notify("saving")
            stats = repo.commit_window(window.fund, records, result.run_id, result.coverage)
            saved = True
            result.data_saved = True
            for key in ("inserted", "updated", "unchanged", "total"):
                setattr(result, key, stats[key])
            print(f"数据库已保存：新增 {result.inserted}，修订 {result.updated}，未变化 {result.unchanged}，累计 {result.total}。", flush=True)
            notify("exporting")
            _export(repo, config, result)
        except FundlabError as error:
            result.status = "export_failed" if saved else "failed"
            result.error_code, result.message = error.code, str(error)
        except (sqlite3.Error, OSError):
            result.status = "export_failed" if saved else "failed"
            result.error_code = "EXPORT_ERROR" if saved else "DATABASE_OR_IO_ERROR"
            result.message = "数据已保存，后续操作失败，可重试导出。" if saved else "本次更新未完成，请检查磁盘、文件占用和数据库访问权限；原有数据保留。"
        except KeyboardInterrupt:
            result.status, result.error_code = "interrupted", "INTERRUPTED"
            result.message = "本人中断；" + ("本次数据已经提交，可重新导出。" if saved else "未提交本次数据。")
            repo.finish_run(result.to_dict())
            raise
        except Exception:
            result.status = "export_failed" if saved else "failed"
            result.error_code = "UNEXPECTED_ERROR"
            result.message = "更新未全部完成，原有数据库记录保留。请查看任务记录并重试。"
        repo.finish_run(result.to_dict())
        return result


def retry_export(config: AppConfig, code: str, *, trigger: str = "manual", run_id: str | None = None) -> UpdateResult:
    """只读数据库重建导出文件，不启动 Edge、不访问网站。"""
    code = validate_code(code)
    if not config.database_path.exists():
        raise FundlabError("NO_DATA", "还没有本地数据库，请先更新基金数据。")
    with update_lock(config):
        repo = Repository(config.database_path)
        if repo.get_fund(code) is None:
            raise FundlabError("NO_DATA", "本地没有该基金，请先更新。")
        result = UpdateResult(run_id or str(uuid4()), code, coverage=repo.latest_coverage(code))
        repo.start_run(result.run_id, code, "export", trigger)
        try:
            _export(repo, config, result)
        except KeyboardInterrupt:
            result.status, result.error_code = "interrupted", "INTERRUPTED"
            result.message = "本人中断导出；数据库数据保留。"
            repo.finish_run(result.to_dict())
            raise
        repo.finish_run(result.to_dict())
        return result
