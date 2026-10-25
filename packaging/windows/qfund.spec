# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os
import sysconfig
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).resolve().parents[1]
datas = collect_data_files('fundlab', includes=['analysis/*.js', 'web/static/*', 'scheduling/*.json', 'scheduling/*.ps1'])
license_bundle = Path(os.environ['QFUND_LICENSE_BUNDLE'])
for license_file in license_bundle.rglob('*'):
    if license_file.is_file():
        datas.append((str(license_file), str(Path('qfund-licenses') / license_file.relative_to(license_bundle).parent)))
# Plotly 7通过运行时属性加载图形模块；旧通用hook不包含validators/_validators.json。
datas += collect_data_files('plotly', includes=['validators/*.json'])
# 部分构建解释器的ssl模块读取同目录的公开CA资源；保留其原有校验行为。
# 标准CPython不存在此文件时无需附加，不依赖维护者机器上的绝对路径。
runtime_ca = Path(sysconfig.get_path('stdlib')) / 'codex-rejected-cas.pem'
if runtime_ca.is_file():
    datas.append((str(runtime_ca), '.'))
for package in ('fundlab-local', 'playwright', 'openpyxl', 'plotly'):
    datas += copy_metadata(package)

a = Analysis([str(root / 'scripts/frozen_entry.py')], pathex=[str(root / 'src')],
             binaries=[], datas=datas,
             hiddenimports=['playwright.sync_api', 'openpyxl', 'plotly.graph_objects'] + collect_submodules('plotly.graph_objs'),
             hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='QFund',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True,
          icon=str(root / 'packaging/windows/assets/qfund.ico'))
worker = Analysis([str(root / 'scripts/frozen_worker_entry.py')], pathex=[str(root / 'src')],
                  binaries=[], datas=[], hiddenimports=[], hookspath=[], hooksconfig={},
                  runtime_hooks=[], excludes=[], noarchive=False)
worker_pyz = PYZ(worker.pure)
worker_exe = EXE(worker_pyz, worker.scripts, [], exclude_binaries=True, name='QFundWorker',
                 debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, worker_exe, a.binaries, a.datas, worker.binaries, worker.datas,
               strip=False, upx=False, name='QFund')
