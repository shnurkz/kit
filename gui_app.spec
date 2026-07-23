# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all, copy_metadata

datas = [
    ('main.py', '.'),
    ('core_updater.py', '.'),
    ('matcher.py', '.'),
    ('stop_brands.txt', '.'),
    ('.env', '.'),
]

if os.path.exists('mapping.db'):
    datas.append(('mapping.db', '.'))

binaries = []
hiddenimports = [
    'streamlit',
    'streamlit.web.cli',
    'streamlit.runtime.scriptrunner.magic_funcs',
    'numpy',
    'pandas',
    'playwright',
    'playwright.async_api',
    'playwright.sync_api',
    'lxml',
    'openpyxl',
    'requests',
    'bs4',
    'yaml',
    'supabase',
    'core_updater',
    'matcher'
]

# Collect all modules, C-extensions, binaries, data files for core heavy packages
for pkg in ['numpy', 'pandas', 'streamlit', 'lxml', 'openpyxl', 'playwright', 'requests', 'supabase', 'pyarrow', 'altair']:
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

def safe_copy_metadata(pkg):
    try:
        return copy_metadata(pkg)
    except Exception:
        return []

for pkg in ['streamlit', 'tqdm', 'regex', 'requests', 'filelock', 'packaging', 'click', 'altair', 'pyarrow', 'numpy', 'pandas']:
    datas += safe_copy_metadata(pkg)

a = Analysis(
    ['run_gui.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Kit_GUI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
