# -*- mode: python ; coding: utf-8 -*-
import sys

a = Analysis(
    ['_launch.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('docs/assets/logo_transparent.ico', 'juxt/assets'),
        ('docs/assets/logo_transparent.png', 'juxt/assets'),
        ('docs/assets/logo_large_transparent.png', 'juxt/assets'),
    ],
    # paramiko is imported inside a function body; PyInstaller won't find it
    # statically, so we declare it explicitly to include SSH support.
    hiddenimports=['paramiko'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='juxt',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX can trigger antivirus false positives on Windows
    console=False,  # no terminal window on Windows / macOS
    icon='docs/assets/logo_transparent.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='juxt',
)

# macOS: wrap the collected bundle into a .app directory
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='juxt.app',
        icon='docs/assets/logo_transparent.ico',
        bundle_identifier='io.github.colinmoldenhauer.juxt',
    )
