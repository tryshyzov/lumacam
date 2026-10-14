# -*- mode: python ; coding: utf-8 -*-
# The macOS app bundle; LumaLinkStudio.spec builds the Windows executable.

from camera_studio import __version__

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('assets/LumaCam.png', 'assets')],
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
    name='LumaLink Studio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='LumaLink Studio',
)

app = BUNDLE(
    coll,
    name='LumaLink Studio.app',
    icon='assets/LumaLinkStudio.icns',
    bundle_identifier='com.lumalink.studio',
    version=__version__,
    info_plist={
        'CFBundleDisplayName': 'LumaLink Studio',
        # The adaptive icon in Assets.car (scripts/build-macos.sh compiles it
        # from assets/LumaLinkStudio.icon); macOS before 26 uses the .icns.
        'CFBundleIconName': 'LumaLinkStudio',
        'CFBundleShortVersionString': __version__,
        'CFBundleVersion': __version__,
        'LSMinimumSystemVersion': '13.0',
        'NSHighResolutionCapable': True,
        # The phone is reached over the local network (Wi-Fi or USB tethering).
        'NSLocalNetworkUsageDescription': (
            'LumaLink Studio receives the video from your phone '
            'over the local network.'
        ),
    },
)
