# -*- mode: python ; coding: utf-8 -*-
# The macOS app bundle; LumaLinkStudio.spec builds the Windows executable.

from camera_studio import __version__

# ADB is the VPN-proof fallback for the USB transport, and on macOS the usual
# USB route: most phones' tethering has no macOS driver. The universal binary
# comes from scripts/fetch-platform-tools.sh; see vendor/README.md.
adb_binaries = [('vendor/platform-tools/adb', 'platform-tools')]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=adb_binaries,
    datas=[
        ('assets/LumaCam.png', 'assets'),
        ('assets/models', 'assets/models'),
        ('vendor/platform-tools/NOTICE.txt', 'platform-tools'),
    ],
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
