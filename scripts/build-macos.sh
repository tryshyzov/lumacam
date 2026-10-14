#!/bin/sh
# Builds dist/LumaLink Studio.app and a disk image to share it.
# Run from the project root with the dependencies and PyInstaller installed:
#   python3 -m pip install -r requirements.txt pyinstaller
#   scripts/build-macos.sh
set -eu

python="${PYTHON:-python3}"
version=$("$python" -c 'from camera_studio import __version__; print(__version__)')

"$python" -m PyInstaller --noconfirm --clean LumaLinkStudio-macos.spec
app="dist/LumaLink Studio.app"

# The icon that follows the macOS appearance (light, dark, tinted, clear) is
# compiled by Xcode's actool; without Xcode the app keeps the .icns icon.
developer_dir="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
if DEVELOPER_DIR="$developer_dir" xcrun --find actool >/dev/null 2>&1; then
    icon_build=$(mktemp -d)
    DEVELOPER_DIR="$developer_dir" xcrun actool assets/LumaLinkStudio.icon \
        --compile "$icon_build" --platform macosx --minimum-deployment-target 13.0 \
        --app-icon LumaLinkStudio --output-partial-info-plist "$icon_build/icon.plist" \
        --errors --warnings >/dev/null
    cp "$icon_build/Assets.car" "$app/Contents/Resources/"
    rm -rf "$icon_build"
    # Adding a file breaks the bundle's ad hoc signature; sign it again.
    codesign --force --deep --sign - "$app"
else
    echo "Xcode not found: building with the .icns icon only" >&2
fi

# The disk image holds the app and a shortcut to Applications to drag it to.
staging=$(mktemp -d)
trap 'rm -rf "$staging"' EXIT
cp -R "$app" "$staging/"
ln -s /Applications "$staging/Applications"
image="dist/LumaLinkStudio-$version-macos.dmg"
rm -f "$image"
hdiutil create -quiet -volname "LumaLink Studio" -srcfolder "$staging" \
    -fs HFS+ -format UDZO "$image"
echo "Built $image"
