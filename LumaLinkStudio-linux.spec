# -*- mode: python ; coding: utf-8 -*-
# Linux build: a folder (not one file), which packaging/linux/build.sh turns
# into the AppImage and the .deb.

import os

# ADB is the VPN-proof fallback for the USB transport; CI downloads the Linux
# platform-tools into vendor/ (see vendor/README.md).
adb_binaries = (
    [('vendor/platform-tools/adb', 'platform-tools')]
    if os.path.isfile('vendor/platform-tools/adb')
    else []
)

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
    [],
    exclude_binaries=True,
    name='LumaLinkStudio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='LumaLinkStudio',
)
