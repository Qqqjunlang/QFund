"""Windows源码安装入口；仅操作项目虚拟环境，不启动业务。"""
import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import tempfile


class InstallError(Exception):
    pass


def run(command, *, cwd, capture=False):
    result = subprocess.run(command, cwd=str(cwd), text=True, encoding="utf-8",
                            errors="replace", capture_output=capture)
    if result.returncode:
        if capture and result.stdout:
            print(result.stdout, flush=True)
        raise InstallError("检查或安装失败，请查看上方提示。已有环境和数据均保留。")
    return result.stdout if capture else None


@contextmanager
def installation_lock(root):
    import msvcrt
    directory = root / ".local"
    if directory.is_symlink() or (hasattr(directory, 'is_junction') and directory.is_junction()):
        raise InstallError(".local 是目录链接，请先核对项目路径，未安装。")
    directory.mkdir(exist_ok=True)
    with (directory / "install.lock").open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise InstallError("另一个安装过程正在运行，请等待结束后重试。") from None
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


# 在目标解释器内核对，避免误用维护者当前环境的可编辑安装。
VERIFY = r'''
import importlib.metadata as metadata
from pathlib import Path
import sys
root = Path(sys.argv[1]).resolve()
import fundlab
from fundlab.config import AppConfig
from fundlab.collectors.session import find_edge
from fundlab.launcher import check_environment
assert Path(fundlab.__file__).resolve() == root / 'src/fundlab/__init__.py', '项目导入来自其他目录'
assert AppConfig.default().root.resolve() == root, '数据根目录不匹配'
assert Path(sys.prefix).resolve() == root / '.venv', '不是本项目虚拟环境'
for line in (root / 'requirements.lock.txt').read_text(encoding='utf-8-sig').splitlines():
    line = line.strip()
    if line and not line.startswith('#'):
        name, expected = line.split('==')
        assert metadata.version(name) == expected, '依赖版本不匹配：' + name
import tomllib
expected = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8-sig'))['project']['version']
assert metadata.version('fundlab-local') == expected, '项目版本不匹配'
check_environment()
assert find_edge(), '未找到Microsoft Edge，请安装Edge后重试'
for name in ('analysis/premium_client.js', 'web/static/index.html', 'web/static/style.css',
             'web/static/app.js', 'web/static/qfund-icon.svg', 'scheduling/calendar-2026.json',
             'scheduling/windows_task.ps1'):
    assert (root / 'src/fundlab' / name).is_file(), '缺少程序资源：' + name
print('项目导入、依赖版本、Edge、数据路径与程序资源检查通过。')
'''


def verify(root, python):
    # 仅导入和核对，不初始化数据库、读取任务状态或打开浏览器。
    run([str(python), "-I", "-X", "utf8", "-c", VERIFY, str(root)], cwd=root)
    run([str(python), "-I", "-m", "pip", "--isolated", "check"], cwd=root)


def install(root, wheelhouse=None):
    if os.name != "nt":
        raise InstallError("安装入口当前仅支持Windows。")
    if sys.version_info < (3, 11):
        raise InstallError("需要Python 3.11或更新版本，建议使用已验证目标Python 3.12。")
    root = Path(root).resolve()
    for name in ("pyproject.toml", "requirements.lock.txt", "src/fundlab/__init__.py", "启动工作台.cmd"):
        if not (root / name).is_file():
            raise InstallError("项目文件不完整，请解压完整源码后重试：" + name)
    if wheelhouse is not None:
        wheelhouse = Path(wheelhouse).resolve()
        if not wheelhouse.is_dir():
            raise InstallError("离线依赖目录不存在，未创建环境。")
    # 检查可写性，不提升权限、不改全局环境。
    with tempfile.TemporaryFile(dir=root):
        pass
    environment = root / ".venv"
    if environment.is_symlink() or (hasattr(environment, 'is_junction') and environment.is_junction()):
        raise InstallError(".venv 是目录链接，请先核对，未修改环境。")
    python = environment / "Scripts/python.exe"
    with installation_lock(root):
        if environment.exists():
            if not python.is_file():
                raise InstallError("已有.venv不完整，已保留。请按安装说明准备新目录，不覆盖旧环境。")
            print("已有项目环境，仅检查，不重新安装或覆盖。", flush=True)
            verify(root, python)
        else:
            print("正在创建本项目 .venv 环境……", flush=True)
            run([sys.executable, "-I", "-m", "venv", str(environment)], cwd=root)
            options = (["--no-index", "--find-links", str(wheelhouse)] if wheelhouse else
                       ["--index-url", "https://pypi.org/simple"])
            print("正在安装依赖（可能需要数分钟）……", flush=True)
            run([str(python), "-I", "-m", "pip", "--isolated", "install", "--disable-pip-version-check",
                 *options, "-r", str(root / "requirements.lock.txt")], cwd=root)
            run([str(python), "-I", "-m", "pip", "--isolated", "install", "--disable-pip-version-check",
                 "--no-index", "--no-build-isolation", "--no-deps", "-e", str(root)], cwd=root)
            verify(root, python)
    print("安装检查完成。请双击“启动工作台.cmd”，首次使用由本人登录并获取数据。", flush=True)
    print("未启动浏览器、获取数据或启用自动更新。", flush=True)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="QFund 项目环境安装与检查")
    parser.add_argument("--wheelhouse", help="维护用：仅从指定本地目录安装依赖，不联网")
    args = parser.parse_args(argv)
    try:
        install(Path(__file__).resolve().parents[1], args.wheelhouse)
    except (InstallError, OSError) as error:
        print("安装未完成：" + str(error), file=sys.stderr, flush=True)
        print("请保留现有 .venv、data 和 .local；修复步骤见 docs/installation.md。", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("安装已中断，已生成的环境保留；请按安装说明核对，不直接覆盖。", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
