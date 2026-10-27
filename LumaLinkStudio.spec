# -*- mode: python ; coding: utf-8 -*-

from camera_studio.adb import ADB_FILES

# ADB is the VPN-proof fallback for the USB transport; see vendor/README.md.
adb_binaries = [(f'vendor/platform-tools/{name}', 'platform-tools') for name in ADB_FILES]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=adb_binaries,
    datas=[('assets/LumaCam.png', 'assets'), ('assets/models', 'assets/models')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter'],
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
    name='LumaLinkStudio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    icon='assets/LumaLinkStudio.ico',
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
