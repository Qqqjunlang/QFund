"""无控制台EXE执行入口；复用同一调度执行器，不启动网页。"""
import argparse
from pathlib import Path
import sys

from fundlab.config import AppConfig
from fundlab.installation import runtime_guard, maintenance_main
from fundlab.scheduling.worker import main as worker_main


def main(argv=None):
    parser = argparse.ArgumentParser(description='QFund 自动获取基金数据后台执行器')
    parser.add_argument('--project-root', type=Path, required=True)
    parser.add_argument('--maintenance', choices=('install', 'uninstall'))
    parser.add_argument('--install-dir', type=Path)
    parser.add_argument('--install-id')
    parser.add_argument('--channel', type=Path)
    args = parser.parse_args(argv)
    if not args.project_root.is_absolute():
        parser.error('project-root须为明确的绝对数据根目录，不使用当前工作目录推断。')
    config = AppConfig(args.project_root.resolve())
    if args.maintenance:
        if (not getattr(sys, 'frozen', False) or not args.install_id or args.install_dir is None or
            not args.install_dir.is_absolute() or args.channel is None or not args.channel.is_absolute()):
            parser.error('安装维护须使用打包入口并指定完整安装信息。')
        return maintenance_main(config, args.install_dir, args.install_id, args.maintenance, args.channel)
    if args.install_dir is not None or args.install_id or args.channel is not None:
        parser.error('安装维护参数不能用于普通采集。')
    try:
        with runtime_guard(config):
            return worker_main(['--project-root', str(config.root)])
    except Exception:
        # 安装持锁时不进入业务执行器，不消耗采集预算；Windows按已有规则有限重试。
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
