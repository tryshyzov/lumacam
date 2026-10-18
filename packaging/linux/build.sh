#!/bin/sh
# Builds LumaLink Studio for Linux: dist/LumaLinkStudio-<version>-x86_64.AppImage
# and dist/lumalink-studio_<version>_amd64.deb, from the repository root:
#
#     sh packaging/linux/build.sh 0.7.0
#
# Needs the project's requirements, pyinstaller, dpkg-deb and either
# appimagetool on PATH or network access to download it.
set -eu

VERSION="${1:?usage: build.sh <version>}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HERE="$ROOT/packaging/linux"
DIST="$ROOT/dist"
cd "$ROOT"

python -m PyInstaller --noconfirm --clean LumaLinkStudio-linux.spec
APP="$DIST/LumaLinkStudio"

icon() {
    python - "$1" "$2" <<'EOF'
import sys
from PIL import Image
Image.open("assets/logo/logo-icon.png").resize((int(sys.argv[2]),) * 2, Image.LANCZOS).save(sys.argv[1])
EOF
}

# --- AppImage ---------------------------------------------------------------
APPDIR="$DIST/LumaLinkStudio.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib"
cp -a "$APP" "$APPDIR/usr/lib/lumalink-studio"
cp "$HERE/lumalink-studio.desktop" "$APPDIR/lumalink-studio.desktop"
sed -i 's|^Exec=.*|Exec=LumaLinkStudio|' "$APPDIR/lumalink-studio.desktop"
icon "$APPDIR/lumalink-studio.png" 256
cp "$APPDIR/lumalink-studio.png" "$APPDIR/.DirIcon"
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/lumalink-studio/LumaLinkStudio" "$@"
EOF
chmod +x "$APPDIR/AppRun"

APPIMAGETOOL="$(command -v appimagetool || true)"
if [ -z "$APPIMAGETOOL" ]; then
    APPIMAGETOOL="$DIST/appimagetool"
    curl -fsSL -o "$APPIMAGETOOL" \
        https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
    chmod +x "$APPIMAGETOOL"
fi
# Runs without FUSE (CI containers have none).
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$APPIMAGETOOL" --no-appstream \
    "$APPDIR" "$DIST/LumaLinkStudio-$VERSION-x86_64.AppImage"

# --- .deb -------------------------------------------------------------------
DEB="$DIST/deb"
rm -rf "$DEB"
mkdir -p "$DEB/DEBIAN" "$DEB/opt" "$DEB/usr/bin" "$DEB/usr/share/applications" \
    "$DEB/usr/share/icons/hicolor/256x256/apps" "$DEB/etc/modprobe.d" "$DEB/etc/modules-load.d"
cp -a "$APP" "$DEB/opt/lumalink-studio"
ln -s /opt/lumalink-studio/LumaLinkStudio "$DEB/usr/bin/lumalink-studio"
install -m 644 "$HERE/lumalink-studio.desktop" "$DEB/usr/share/applications/"
icon "$DEB/usr/share/icons/hicolor/256x256/apps/lumalink-studio.png" 256
install -m 644 "$HERE/modprobe-lumalink.conf" "$DEB/etc/modprobe.d/lumalink.conf"
echo v4l2loopback > "$DEB/etc/modules-load.d/lumalink.conf"
sed "s/@VERSION@/$VERSION/; s/@SIZE@/$(du -sk "$DEB" | cut -f1)/" "$HERE/control" > "$DEB/DEBIAN/control"
printf '/etc/modprobe.d/lumalink.conf\n/etc/modules-load.d/lumalink.conf\n' > "$DEB/DEBIAN/conffiles"
install -m 755 "$HERE/postinst" "$DEB/DEBIAN/postinst"
dpkg-deb --build --root-owner-group "$DEB" "$DIST/lumalink-studio_${VERSION}_amd64.deb"

ls -l "$DIST"/*.AppImage "$DIST"/*.deb
