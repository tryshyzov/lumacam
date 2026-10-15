#!/bin/sh
# Builds dist/LumaLink Studio.app and a disk image to share it.
# Run from the project root with the dependencies and PyInstaller installed:
#   python3 -m pip install -r requirements.txt pyinstaller
#   scripts/build-macos.sh
set -eu

python="${PYTHON:-python3}"
version=$("$python" -c 'from camera_studio import __version__; print(__version__)')
# The app runs on the processor its Python was built for: Apple silicon or
# Intel. Build on each (CI does) for both downloads.
arch=$("$python" -c 'import platform; print(platform.machine())')
case "$arch" in
    arm64) arch_name=apple-silicon ;;
    x86_64) arch_name=intel ;;
    *) echo "Unsupported architecture: $arch" >&2; exit 1 ;;
esac

if [ ! -x vendor/platform-tools/adb ]; then
    scripts/fetch-platform-tools.sh
fi

"$python" -m PyInstaller --noconfirm --clean LumaLinkStudio-macos.spec
app="dist/LumaLink Studio.app"

# Xcode when it is installed; the Command Line Tools otherwise.
developer_dir="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
[ -d "$developer_dir" ] || developer_dir=$(xcode-select -p)
export DEVELOPER_DIR="$developer_dir"

# The icon that follows the macOS appearance (light, dark, tinted, clear) is
# compiled by Xcode's actool; without Xcode the app keeps the .icns icon.
if xcrun --find actool >/dev/null 2>&1; then
    icon_build=$(mktemp -d)
    xcrun actool assets/LumaLinkStudio.icon \
        --compile "$icon_build" --platform macosx --minimum-deployment-target 13.0 \
        --app-icon LumaLinkStudio --output-partial-info-plist "$icon_build/icon.plist" \
        --errors --warnings >/dev/null || true
    # Xcode before 26 has actool but cannot read Icon Composer icons.
    if [ -f "$icon_build/Assets.car" ]; then
        cp "$icon_build/Assets.car" "$app/Contents/Resources/"
    else
        echo "This Xcode cannot compile assets/LumaLinkStudio.icon: building with the .icns icon only" >&2
    fi
    rm -rf "$icon_build"
else
    echo "Xcode not found: building with the .icns icon only" >&2
fi

# LumaLink Camera: the extension goes where macOS looks for the app's system
# extensions, and its helper next to the app's executable, so that macOS
# treats it as part of the app. See macvirtualcam/README.md.
LUMALINK_VERSION="$version" MACOS_TARGET="$arch-apple-macos13.0" macvirtualcam/build.sh
extension="$app/Contents/Library/SystemExtensions/com.lumalink.studio.camera.systemextension"
mkdir -p "$(dirname "$extension")"
cp -R macvirtualcam/build/com.lumalink.studio.camera.systemextension "$extension"
cp macvirtualcam/build/lumalink-camera "$app/Contents/MacOS/"

# Everything inside is signed first, then the extension again with its own
# entitlements, then the app. Only a Developer Program identity may grant the
# app the right to install the extension; ad hoc builds go without it.
identity="${MACOS_SIGN_IDENTITY:--}"
codesign --force --deep --options runtime --sign "$identity" "$app"
codesign --force --options runtime --sign "$identity" \
    --entitlements macvirtualcam/build/Extension.entitlements "$extension"
if [ "$identity" = "-" ]; then
    codesign --force --sign - "$app"
else
    if [ -n "${MACOS_PROVISIONING_PROFILE:-}" ]; then
        cp "$MACOS_PROVISIONING_PROFILE" "$app/Contents/embedded.provisionprofile"
    fi
    codesign --force --options runtime --sign "$identity" \
        --entitlements macvirtualcam/App.entitlements "$app"
fi

# The disk image holds the app and a shortcut to Applications to drag it to.
staging=$(mktemp -d)
trap 'rm -rf "$staging"' EXIT
cp -R "$app" "$staging/"
ln -s /Applications "$staging/Applications"
image="dist/LumaLinkStudio-$version-macos-$arch_name.dmg"
rm -f "$image"
hdiutil create -quiet -volname "LumaLink Studio" -srcfolder "$staging" \
    -fs HFS+ -format UDZO "$image"
echo "Built $image"
