#!/bin/sh
# Builds dist/LumaLink Studio.app and a disk image to share it.
# Run from the project root with the dependencies and PyInstaller installed:
#   python3 -m pip install -r requirements.txt pyinstaller
#   scripts/build-macos.sh
set -eu

python="${PYTHON:-python3}"
version=$("$python" -c 'from camera_studio import __version__; print(__version__)')

"$python" -m PyInstaller --noconfirm --clean LumaLinkStudio-macos.spec

# The disk image holds the app and a shortcut to Applications to drag it to.
staging=$(mktemp -d)
trap 'rm -rf "$staging"' EXIT
cp -R "dist/LumaLink Studio.app" "$staging/"
ln -s /Applications "$staging/Applications"
image="dist/LumaLinkStudio-$version-macos.dmg"
rm -f "$image"
hdiutil create -quiet -volname "LumaLink Studio" -srcfolder "$staging" \
    -fs HFS+ -format UDZO "$image"
echo "Built $image"
