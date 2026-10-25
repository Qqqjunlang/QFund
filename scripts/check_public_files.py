"""白名单公开范围和高置信度泄漏检查；报告位置，不输出疑似凭据值。"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

GENERATED = {'__pycache__', '.pytest_cache'}
SECRET_PATTERNS = {
    'private-key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'github-token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b'),
    'aws-access-key': re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'openai-key': re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{36,}\b'),
    'personal-windows-path': re.compile(r'(?i)\b[A-Z]:[\\/](?:Users[\\/](?!Public\b|Default\b)[^\\/\s]+|codex_works[\\/])'),
}


def public_files(root, *, require_license=True):
    root = Path(root).resolve()
    config = json.loads((root / 'packaging/public-files.json').read_text(encoding='utf-8-sig'))
    paths = set()
    for name in config['root_files']:
        path = root / name
        if name == 'LICENSE' and not require_license and not path.exists():
            continue
        if not path.is_file():
            raise ValueError('Missing public file: ' + name)
        paths.add(path)
    for name, suffixes in config['trees'].items():
        for path in (root / name).rglob('*'):
            relative = path.relative_to(root)
            if any(part in GENERATED or part.endswith('.egg-info') for part in relative.parts):
                continue
            if path.is_dir():
                if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                    raise ValueError('Linked directory: ' + relative.as_posix())
                continue
            if path.suffix not in suffixes:
                raise ValueError('Unexpected file in public tree: ' + relative.as_posix())
            if path.name.lower() in {'credentials.json','cookies.json','storage-state.json','auth.json'}:
                raise ValueError('Private-state filename in public tree: ' + relative.as_posix())
            paths.add(path)
    for name in config['docs']:
        path = root / 'docs' / name
        if not path.is_file():
            raise ValueError('Missing public document: ' + name)
        paths.add(path)
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('Linked public file: ' + path.relative_to(root).as_posix())
        if path.stat().st_size > 10 * 1024 * 1024:
            raise ValueError('Large repository file; use Releases: ' + path.relative_to(root).as_posix())
    return sorted(paths, key=lambda p: p.relative_to(root).as_posix())


def inspect(root, *, require_license=True):
    root = Path(root).resolve()
    paths = public_files(root, require_license=require_license)
    findings = []
    for path in paths:
        if path.suffix == '.ico':
            continue
        content = path.read_text(encoding='utf-8-sig')
        for kind, pattern in SECRET_PATTERNS.items():
            for match in pattern.finditer(content):
                findings.append(dict(file=path.relative_to(root).as_posix(),
                    line=content.count('\n', 0, match.start()) + 1, kind=kind))
    # 忽略规则不能保护已跟踪文件；有仓库时另外核对跟踪集合。
    tracked_checked = (root / '.git').exists()
    if tracked_checked:
        listed = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z'], encoding='utf-8')
        allowed = {p.relative_to(root).as_posix() for p in paths}
        for name in listed.split('\0'):
            if name and name not in allowed:
                findings.append(dict(file=name, line=0, kind='tracked-outside-public-scope'))
    return paths, findings, tracked_checked


def main():
    parser = argparse.ArgumentParser(description='Check QFund public repository files')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--allow-pending-license', action='store_true', help='仅整理时使用；发布不得带此参数')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    try:
        paths, findings, tracked = inspect(args.root, require_license=not args.allow_pending_license)
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr); return 1
    report = dict(files=len(paths), tracked_files_checked=tracked, findings=findings,
        license_pending=not (args.root / 'LICENSE').exists(),
        manifest=[dict(path=p.relative_to(args.root.resolve()).as_posix(), bytes=p.stat().st_size,
                       sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths])
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k != 'manifest'}, ensure_ascii=False, indent=2))
    return 1 if findings else 0


if __name__ == '__main__':
    raise SystemExit(main())
