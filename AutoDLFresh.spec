# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
from pathlib import Path
import sys

datas = [('autodl_fresh/data/gpu_quotes.json', 'autodl_fresh/data')]
binaries = []
hiddenimports = []
tmp_ret = collect_all('playwright')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['run_app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6'],
    noarchive=False,
    optimize=0,
)
# Qt on supported Windows versions uses the OS ICU API. A different ICU DLL
# discovered on PATH (for example from another Python package) has versioned
# exports and breaks QtCore at startup. Do not ship that unrelated implementation.
if sys.platform == 'win32':
    # Optional PDF and virtual-keyboard plugins bring unused GPL-only modules
    # and QML/Quick dependencies. The desktop app uses Widgets and SVG only.
    unused_qt = {
        'qt6pdf.dll', 'qpdf.dll', 'qt6virtualkeyboard.dll', 'qtvirtualkeyboardplugin.dll',
        'qt6qml.dll', 'qt6qmlmeta.dll', 'qt6qmlmodels.dll',
        'qt6qmlworkerscript.dll', 'qt6quick.dll',
    }
    a.binaries = [
        item for item in a.binaries
        if Path(item[0]).name.lower() != 'icuuc.dll'
        and not Path(item[0]).name.lower().startswith('icudt')
        and Path(item[0]).name.lower() not in unused_qt
    ]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AutoDLFresh',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\app.ico'],
    manifest='assets/windows.manifest',
    version='assets/version_info.txt',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AutoDLFresh',
)
