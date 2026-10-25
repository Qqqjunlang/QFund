"""系统任务无控制台入口；保留一次执行的结果，不常驻。"""
import argparse
import json
from pathlib import Path
import sys
from datetime import datetime, timezone

from fundlab.config import AppConfig
from fundlab.errors import FundlabError
from fundlab.scheduling.runner import run_due_updates


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-root', required=True)
    args = parser.parse_args(argv)
    config = AppConfig(Path(args.project_root).resolve())
    started = datetime.now(timezone.utc).isoformat()
    try:
        # 日志目标不可写时不启动任何采集。
        config.local_dir.mkdir(parents=True, exist_ok=True)
        with (config.local_dir / 'scheduled-worker.jsonl').open('a', encoding='utf-8') as log:
            log.write(json.dumps(dict(event='started', at=started)) + '\n'); log.flush()
            try:
                result = run_due_updates(config)
                code = 0 if result['status'] in {'succeeded','not_due','skipped_empty','skipped_closed'} else 2
            except FundlabError as error:
                result = dict(status='failed', error_code=error.code, message=str(error)); code = 1
            except Exception:
                result = dict(status='failed', error_code='WORKER_FAILED', message='自动执行中断，请在工作台核对执行记录；原认领已保留。'); code = 1
            log.write(json.dumps(dict(event='finished', at=datetime.now(timezone.utc).isoformat(), result=result, exit_code=code), ensure_ascii=False) + '\n')
            log.flush()
            return code
    except OSError:
        return 1


if __name__ == '__main__':
    sys.exit(main())
