"""用Inno Setup构建当前用户安装包，按清单打包，不覆盖历史产物。"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description='生成QFund安装向导EXE')
    parser.add_argument('--program-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--compiler', type=Path)
    parser.add_argument('--test-root', type=Path, help='维护验证用：独立登记、数据与快捷方式根目录，须在项目.local内')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    program, output = args.program_dir.resolve(), args.output_dir.resolve()
    if sys.platform != 'win32' or not all((program / n).is_file() for n in ('QFund.exe','QFundWorker.exe')):
        parser.error('需要完整Windows程序目录，包含QFund.exe、QFundWorker.exe及_internal。')
    if output.exists(): parser.error('输出目录已存在，请另选目录，旧产物保留。')
    compiler = args.compiler or shutil.which('ISCC.exe')
    if not compiler or not Path(compiler).is_file(): parser.error('请准备Inno Setup编译器，并用--compiler指定ISCC.exe。')
    stage = root / '.local' / 'installer-staging' / uuid4().hex
    stage.mkdir(parents=True)
    records = []
    for file in sorted(program.rglob('*')):
        if not file.is_file(): continue
        relative = file.relative_to(program)
        if relative.parts[0] not in {'QFund.exe','QFundWorker.exe','_internal'}: continue
        if file.is_symlink() or not file.resolve().is_relative_to(program): parser.error('程序包不允许包含外部链接。')
        target = stage / relative; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, target)
        records.append(dict(path=relative.as_posix(),sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
    # 安装引擎不混入源码依赖；在候选包中保留其许可与归属。
    inno_license = Path(compiler).resolve().parent / 'license.txt'
    if not inno_license.is_file():
        parser.error('Inno Setup编译器目录缺少license.txt，请使用完整官方编译器目录。')
    relative = Path('_internal/qfund-licenses/inno-setup/LICENSE.txt')
    target = stage / relative; target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(inno_license, target)
    records.append(dict(path=relative.as_posix(),sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
    version = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8-sig'))['project']['version']
    (stage / 'program-manifest.json').write_text(json.dumps(dict(schema=1, product='QFund', version=version, files=records), ensure_ascii=False, indent=2),encoding='utf-8')
    file_list = [item['path'] for item in records] + ['program-manifest.json','bundle-files.txt']
    (stage / 'bundle-files.txt').write_text('\n'.join(file_list),encoding='utf-8-sig')
    install_id = 'QFund.Desktop'
    (stage / 'qfund-install.json').write_text(json.dumps(dict(product='QFund',schema=1,pending=True)),encoding='utf-8')
    defines = ['/DSourceDir='+str(stage), '/DPackagingDir='+str(root/'packaging/windows'),
               '/DOutputDir='+str(output), '/DAppVersion='+version]
    if args.test_root:
        test_root = args.test_root.resolve()
        if not test_root.is_relative_to(root / '.local') or test_root == root / '.local':
            parser.error('test-root须位于项目.local的独立子目录。')
        install_id = 'QFund.Test.' + hashlib.sha256(str(test_root).encode()).hexdigest()[:12]
        defines.append('/DTestRoot='+str(test_root))
    defines.append('/DInstallId='+install_id)
    code = subprocess.call([str(compiler), *defines, str(root/'packaging/windows/installer.iss')],cwd=root)
    if code == 0:
        files = [dict(name=p.name,bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in output.glob('*.exe')]
        (output/'installer-manifest.json').write_text(json.dumps(dict(product='QFund',version=version,install_id=install_id,
            test_build=bool(args.test_root),official_release=False,files=files),ensure_ascii=False,indent=2),encoding='utf-8')
    return code


if __name__ == '__main__': raise SystemExit(main())
