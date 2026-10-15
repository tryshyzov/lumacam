#!/bin/sh
# Builds the LumaLink Camera extension and its helper into macvirtualcam/build:
#   com.lumalink.studio.camera.systemextension   goes to Contents/Library/SystemExtensions
#   lumalink-camera                              goes to Contents/MacOS
# scripts/build-macos.sh runs this and puts both into LumaLink Studio.app.
#
# Installing a camera extension needs a build signed by an Apple Developer
# Program team: set MACOS_TEAM_ID and MACOS_SIGN_IDENTITY (for example
# "Developer ID Application: Name (TEAMID)"). Without them the build is signed
# ad hoc; it compiles and the helper runs, but macOS refuses the extension.
set -eu

cd "$(dirname "$0")"
team_id="${MACOS_TEAM_ID:-TEAMID}"
identity="${MACOS_SIGN_IDENTITY:--}"
version="${LUMALINK_VERSION:-0.0.0}"
identifier=com.lumalink.studio.camera
target="${MACOS_TARGET:-arm64-apple-macos13.0}"

rm -rf build
bundle="build/$identifier.systemextension"
mkdir -p "$bundle/Contents/MacOS"

xcrun swiftc -O -target "$target" -swift-version 5 \
    Shared/CameraConfig.swift Extension/*.swift \
    -o "$bundle/Contents/MacOS/$identifier"
xcrun swiftc -O -target "$target" -swift-version 5 \
    Shared/CameraConfig.swift Helper/main.swift \
    -o build/lumalink-camera

# The Mach service name must start with an app group of the signing team.
sed -e "s/@TEAM_ID@/$team_id/g" -e "s/@VERSION@/$version/g" \
    Extension/Info.plist > "$bundle/Contents/Info.plist"
sed -e "s/@TEAM_ID@/$team_id/g" Extension/Extension.entitlements > build/Extension.entitlements

codesign --force --options runtime --sign "$identity" \
    --entitlements build/Extension.entitlements "$bundle"
codesign --force --options runtime --sign "$identity" build/lumalink-camera
echo "Built macvirtualcam/$bundle and macvirtualcam/build/lumalink-camera"
