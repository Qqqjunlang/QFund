"""用独立构建环境生成目录式EXE原型；不安装工具或覆盖旧产物。"""
import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description='构建QFund Windows EXE原型')
    parser.add_argument('--output-dir', type=Path, required=True, help='新的输出目录，不覆盖已有目录')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if sys.platform != 'win32' or importlib.util.find_spec('PyInstaller') is None:
        parser.error('请在独立Windows构建环境安装requirements.lock.txt及packaging/windows/requirements-build.txt。')
    output = args.output_dir.resolve()
    if output.exists():
        parser.error('输出目录已存在，请使用新目录，旧产物保持不变。')
    work = root / '.local' / 'exe-build' / uuid4().hex
    from collect_licenses import collect
    licenses = work / 'licenses'
    collect(root, licenses)
    environment = dict(os.environ, QFUND_LICENSE_BUNDLE=str(licenses))
    return subprocess.call([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                            '--distpath', str(output), '--workpath', str(work),
                            str(root / 'packaging/windows/qfund.spec')], cwd=root, env=environment)


if __name__ == '__main__':
    raise SystemExit(main())
