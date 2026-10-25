"""核对版本和已生成清单后整理待审阅附件；不调用GitHub发布API。"""
import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tomllib
import zipfile


def main():
    parser=argparse.ArgumentParser(description='Collect matching QFund release candidates')
    for name in ('source-dir','installer-dir','wheel-dir','output-dir'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    version=tomllib.loads((root/'pyproject.toml').read_text(encoding='utf-8-sig'))['project']['version']
    try:
        output=args.output_dir.resolve()
        if output.exists() or not output.is_relative_to(root/'dist'):
            raise ValueError('Choose a new output subdirectory in dist/.')
        source_manifest=args.source_dir/'source-manifest.json'
        installer_manifest=args.installer_dir/'installer-manifest.json'
        source=json.loads(source_manifest.read_text(encoding='utf-8'))
        installer=json.loads(installer_manifest.read_text(encoding='utf-8'))
        if source['version']!=version or installer['version']!=version or installer['test_build']:
            raise ValueError('Source/installer version mismatch or isolated test installer.')
        archive=args.source_dir/source['archive']
        if archive.name!=f'QFund-{version}-source.zip' or hashlib.sha256(archive.read_bytes()).hexdigest()!=source['sha256']:
            raise ValueError('Source archive checksum mismatch.')
        exe=args.installer_dir/f'QFund-Setup-{version}.exe'
        entries=[item for item in installer['files'] if item['name']==exe.name]
        if len(entries)!=1 or hashlib.sha256(exe.read_bytes()).hexdigest()!=entries[0]['sha256']:
            raise ValueError('Installer checksum mismatch.')
        wheels=list(args.wheel_dir.glob('fundlab_local-*.whl'))
        if len(wheels)!=1:raise ValueError('Expected exactly one project wheel.')
        with zipfile.ZipFile(wheels[0]) as bundle:
            metadata=BytesParser().parsebytes(bundle.read(next(n for n in bundle.namelist() if n.endswith('.dist-info/METADATA'))))
            if metadata['Version']!=version or metadata['License-Expression']!='MIT':
                raise ValueError('Wheel version or license mismatch.')
        output.mkdir(parents=True)
        files=[archive,source_manifest,exe,installer_manifest,wheels[0]]
        for path in files:shutil.copyfile(path,output/path.name)
        shutil.copyfile(root/'docs/release-notes.md',output/'RELEASE_NOTES.md')
        records=[dict(name=p.name,bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
                 for p in sorted(output.iterdir()) if p.is_file()]
        (output/'release-manifest.json').write_text(json.dumps(dict(product='QFund',version=version,
            tag='v'+version,published=False,files=records),ensure_ascii=False,indent=2),encoding='utf-8')
        checks=records+[dict(name='release-manifest.json',sha256=hashlib.sha256((output/'release-manifest.json').read_bytes()).hexdigest())]
        (output/'SHA256SUMS.txt').write_text(''.join(item['sha256']+'  '+item['name']+'\n' for item in checks),encoding='ascii')
        print(output)
    except (OSError,ValueError,KeyError,StopIteration) as error:
        print(str(error),file=sys.stderr);return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
