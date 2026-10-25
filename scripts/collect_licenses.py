"""从实际构建环境复制上游许可文本；不读取用户数据或下载额外组件。"""
import argparse
import hashlib
from importlib.metadata import distribution
import json
from pathlib import Path
import shutil
import sys
import tomllib

RUNTIME = ('playwright', 'openpyxl', 'plotly', 'et_xmlfile', 'greenlet', 'narwhals',
           'packaging', 'pyee', 'typing_extensions', 'setuptools')
BUILD = ('pyinstaller', 'pyinstaller-hooks-contrib', 'altgraph', 'pefile', 'pywin32-ctypes')


def collect(root, destination):
    root, destination = Path(root), Path(destination)
    if destination.exists():
        raise ValueError('License output already exists; choose a new directory.')
    destination.mkdir(parents=True)
    records = []

    def copy(source, relative, component, version):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        records.append(dict(component=component,version=version,path=relative.as_posix(),
                            sha256=hashlib.sha256(target.read_bytes()).hexdigest()))

    project_version = tomllib.loads((root/'pyproject.toml').read_text(encoding='utf-8-sig'))['project']['version']
    for name in ('LICENSE','THIRD_PARTY_NOTICES.md'):
        copy(root/name,Path('project')/name,'QFund',project_version)
    base = Path(sys.base_prefix)
    python_license = base / 'LICENSE.txt'
    if not python_license.is_file():
        raise ValueError('Python distribution LICENSE.txt is missing; retain the incomplete output for review.')
    copy(python_license,Path('python')/'LICENSE.txt','Python',sys.version.split()[0])
    for name in (*RUNTIME,*BUILD):
        package = distribution(name)
        copied = 0
        for item in package.files or []:
            filename = Path(item).name.lower()
            if not (filename.startswith(('license','licence','copying','notice','authors')) or filename == 'thirdpartynotices.txt'):
                continue
            if Path(item).suffix.lower() in {'.py','.pyc','.pyo'}:
                continue
            relative = Path(item)
            if '..' in relative.parts or relative.is_absolute():
                continue
            source = Path(package.locate_file(item))
            if not source.is_file():
                raise ValueError('Declared license file is missing: ' + name + '/' + relative.as_posix())
            copy(source,Path(name)/relative,name,package.version)
            copied += 1
        if not copied:
            raise ValueError('No upstream license text found: ' + name)
    (destination/'manifest.json').write_text(json.dumps(dict(schema=1,files=records),ensure_ascii=False,indent=2),encoding='utf-8')
    return records


def main():
    parser=argparse.ArgumentParser(description='Collect license texts from the actual EXE build environment')
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    try:
        records=collect(Path(__file__).resolve().parents[1],args.output_dir)
    except (OSError,ValueError) as error:
        print(str(error),file=sys.stderr);return 1
    print('Collected license texts:',len(records))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
