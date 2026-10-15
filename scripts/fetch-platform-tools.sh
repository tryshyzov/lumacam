#!/bin/sh
# Downloads Google's Android SDK Platform-Tools for macOS and copies ADB into
# vendor/platform-tools, where LumaLinkStudio-macos.spec picks it up.
# scripts/fetch-platform-tools.ps1 does the same for Windows.
set -eu

url=https://dl.google.com/android/repository/platform-tools-latest-darwin.zip
target="$(cd "$(dirname "$0")/.." && pwd)/vendor/platform-tools"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

curl --fail --silent --show-error --location "$url" --output "$work/platform-tools.zip"
unzip -q "$work/platform-tools.zip" -d "$work"
mkdir -p "$target"
cp "$work/platform-tools/adb" "$work/platform-tools/NOTICE.txt" "$target/"
echo "ADB copied to $target"
