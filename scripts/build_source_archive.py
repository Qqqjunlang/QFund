"""经公开检查后生成源码ZIP及清单；不复制本机数据，不覆盖旧产物。"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tomllib
import zipfile

from check_public_files import inspect


def main():
    parser = argparse.ArgumentParser(description='Build the reviewed QFund source archive')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        paths, findings, _ = inspect(root)
        if findings:
            raise ValueError('Public-file check failed; run scripts/check_public_files.py for locations.')
        version = tomllib.loads((root/'pyproject.toml').read_text(encoding='utf-8-sig'))['project']['version']
        output = args.output_dir.resolve()
        if output.exists():
            raise ValueError('Output exists; choose a new directory to retain previous artifacts.')
        if not output.is_relative_to(root/'dist'):
            raise ValueError('Use a new subdirectory inside dist/.')
        output.mkdir(parents=True)
        prefix = 'QFund-' + version
        archive = output / (prefix + '-source.zip')
        manifest = []
        with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as bundle:
            for path in paths:
                relative = path.relative_to(root).as_posix()
                content = path.read_bytes()
                bundle.writestr(prefix + '/' + relative, content)
                manifest.append(dict(path=relative, bytes=len(content), sha256=hashlib.sha256(content).hexdigest()))
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (output/'source-manifest.json').write_text(json.dumps(dict(product='QFund',version=version,
            archive=archive.name,sha256=digest,files=manifest),ensure_ascii=False,indent=2),encoding='utf-8')
        (output/'SHA256SUMS.txt').write_text(digest+'  '+archive.name+'\n',encoding='ascii')
        print(archive)
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr); return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
