"""Draws every app icon from one design: the layers in assets/LumaLinkStudio.icon.

That Icon Composer icon is what macOS 26+ shows, in glass. This script flattens
the same layers (the aperture's blades, its hexagonal opening) on the logo's
dark tile, so the other platforms get the same picture:

    assets/LumaLinkStudio.icns   macOS before 26, and builds without Xcode
    assets/LumaLinkStudio.ico    Windows executable and installer
    assets/LumaCam.png           the app window's icon
    site/assets/icon.png         the website's header
    site/assets/favicon.png      the website's tab icon
    android-app/.../mipmap-*     Android adaptive icon, with a themed variant

Sizes of 48 px and less use the small version of the layers in assets/logo
(wider gaps, a larger opening), which stays readable there. The layers come
from scripts/draw-logo.py (see assets/logo/README.md).

Needs macOS (iconutil) and the project's dependencies:

    python3 scripts/make-icons.py
"""

from __future__ import annotations

import io
import struct
import subprocess
import sys
import tempfile
from math import cos, pi, sin
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parents[1]
LAYERS = ROOT / "assets" / "LumaLinkStudio.icon" / "Assets"
SMALL_LAYERS = ROOT / "assets" / "logo" / "layers"
ANDROID_RES = ROOT / "android-app" / "src" / "main" / "res"
SITE_ASSETS = ROOT / "site" / "assets"

# The layers' canvas, with the mark centred on it (see the .icon's SVGs).
CANVAS = 1024
MARK = (LAYERS / "blades.svg", LAYERS / "opening.svg")  # bottom first
SMALL_MARK = (SMALL_LAYERS / "blades-small.svg", SMALL_LAYERS / "opening-small.svg")
DISC_RADIUS = 0.31 * CANVAS
SMALL_SIZE = 48  # this and below take the small mark

BACKGROUND = (0x23, 0x23, 0x23, 255)  # the logo's tile, the .icon's fill
CORNER_RADIUS = 0.23  # of the side, for flat tiles (Windows, window, site)
CORNER_EXPONENT = 5  # macOS: a superellipse close to Apple's continuous corners
SUPERSAMPLE = 4

# Android adaptive icons: a 108 dp layer, of which launchers may show only the
# circle 66 dp across; the disc stays inside it.
ANDROID_DENSITIES = {"mdpi": 1, "hdpi": 1.5, "xhdpi": 2, "xxhdpi": 3, "xxxhdpi": 4}
ANDROID_DISC_RADIUS = 0.255  # of the layer's width
ANDROID_LAUNCHER_DP = 48


def render(layers: tuple[Path, ...], size: int, scale: float) -> Image.Image:
    """Draw ``layers`` centred on a ``size`` square, ``scale`` times their canvas."""
    image = QImage(size, size, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    side = CANVAS * scale
    target = QRectF((size - side) / 2, (size - side) / 2, side, side)
    for path in layers:
        QSvgRenderer(str(path)).render(painter, target)
    painter.end()
    return Image.frombytes("RGBA", (size, size), bytes(image.constBits()))


def superellipse(size: int, body: float) -> Image.Image:
    """A mask of macOS's rounded square, ``body`` wide, centred on ``size``."""
    half = body / 2
    middle = size / 2
    points = []
    for index in range(720):
        angle = 2 * pi * index / 720
        c, s = cos(angle), sin(angle)
        x = half * abs(c) ** (2 / CORNER_EXPONENT) * (1 if c >= 0 else -1)
        y = half * abs(s) ** (2 / CORNER_EXPONENT) * (1 if s >= 0 else -1)
        points.append((middle + x, middle + y))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).polygon(points, fill=255)
    return mask


def rounded(size: int, body: float) -> Image.Image:
    """A mask of the logo's flat tile, ``body`` wide, centred on ``size``."""
    left = (size - body) / 2
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (left, left, left + body, left + body), radius=CORNER_RADIUS * body, fill=255
    )
    return mask


def tile(size: int, body_fraction: float, shadow: bool = False, macos: bool = False,
         small: bool | None = None) -> Image.Image:
    """The mark on its dark tile, the tile ``body_fraction`` of ``size``.

    ``small`` picks the small mark; by default sizes up to SMALL_SIZE get it.
    """
    big = size * SUPERSAMPLE
    body = round(big * body_fraction)
    mask = (superellipse if macos else rounded)(big, body)
    canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    if shadow:
        alpha = mask.point(lambda value: value * 0.3)
        alpha = ImageChops.offset(alpha, 0, round(big * 10 / CANVAS))
        canvas.putalpha(alpha.filter(ImageFilter.GaussianBlur(big * 10 / CANVAS)))
    plate = Image.new("RGBA", (big, big), BACKGROUND)
    mark = SMALL_MARK if (size <= SMALL_SIZE if small is None else small) else MARK
    plate.alpha_composite(render(mark, big, body / CANVAS))
    canvas.paste(plate, (0, 0), mask)
    return canvas.resize((size, size), Image.Resampling.LANCZOS)


def android_layers(size: int, small: bool) -> tuple[Image.Image, Image.Image]:
    """The adaptive icon's foreground and its one-colour themed variant."""
    big = size * SUPERSAMPLE
    scale = ANDROID_DISC_RADIUS * big / DISC_RADIUS
    blades, opening = SMALL_MARK if small else MARK
    foreground = render((blades, opening), big, scale)
    # Themed icons take only the shape: the blades, with the opening a hole.
    monochrome = render((blades,), big, scale)
    white = Image.new("RGBA", (big, big), (255, 255, 255, 0))
    white.putalpha(monochrome.getchannel("A"))
    return (
        foreground.resize((size, size), Image.Resampling.LANCZOS),
        white.resize((size, size), Image.Resampling.LANCZOS),
    )


def write_icns(path: Path) -> None:
    with tempfile.TemporaryDirectory() as folder:
        iconset = Path(folder) / "LumaLinkStudio.iconset"
        iconset.mkdir()
        for points in (16, 32, 128, 256, 512):
            for factor in (1, 2):
                pixels = points * factor
                name = f"icon_{points}x{points}{'@2x' if factor == 2 else ''}.png"
                # Apple's macOS icon grid: an 824 tile on a 1024 canvas.
                tile(pixels, 824 / 1024, shadow=pixels >= 64, macos=True).save(iconset / name)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(path)], check=True)


def write_ico(path: Path, sizes: tuple[int, ...]) -> None:
    """A Windows icon with each size drawn on its own, stored as PNG."""
    # Windows draws icons edge to edge; a small margin keeps the corners clean.
    images = []
    for size in sizes:
        data = io.BytesIO()
        tile(size, 0.94).save(data, format="PNG", optimize=True)
        images.append((size, data.getvalue()))
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, payload = b"", b""
    for size, data in images:
        side = 0 if size >= 256 else size  # 0 means 256
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        payload += data
    path.write_bytes(header + entries + payload)


def write_android() -> None:
    adaptive = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!-- Drawn by scripts/make-icons.py from assets/LumaLinkStudio.icon. -->\n'
        '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '    <background android:drawable="@color/ic_launcher_background" />\n'
        '    <foreground android:drawable="@mipmap/ic_launcher_foreground" />\n'
        '    <monochrome android:drawable="@mipmap/ic_launcher_monochrome" />\n'
        '</adaptive-icon>\n'
    )
    anydpi = ANDROID_RES / "mipmap-anydpi-v26"
    anydpi.mkdir(exist_ok=True)
    for name in ("ic_launcher.xml", "ic_launcher_round.xml"):
        (anydpi / name).write_text(adaptive)
    values = ANDROID_RES / "values"
    values.mkdir(exist_ok=True)
    red, green, blue, _ = BACKGROUND
    (values / "ic_launcher_background.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<resources>\n"
        f'    <color name="ic_launcher_background">#{red:02x}{green:02x}{blue:02x}</color>\n'
        "</resources>\n"
    )
    for density, factor in ANDROID_DENSITIES.items():
        folder = ANDROID_RES / f"mipmap-{density}"
        folder.mkdir(exist_ok=True)
        # The launcher shows the icon at 48 dp: in pixels at this density.
        small = ANDROID_LAUNCHER_DP * factor <= SMALL_SIZE
        foreground, monochrome = android_layers(round(108 * factor), small)
        foreground.save(folder / "ic_launcher_foreground.png", optimize=True)
        monochrome.save(folder / "ic_launcher_monochrome.png", optimize=True)
        # minSdk is 26, so the adaptive icon always replaces the old bitmaps.
        for old in ("ic_launcher.png", "ic_launcher_round.png"):
            (folder / old).unlink(missing_ok=True)


def main() -> None:
    if sys.platform != "darwin":
        sys.exit("iconutil, which builds the .icns, is only on macOS")
    QGuiApplication.instance() or QGuiApplication([])

    write_icns(ROOT / "assets" / "LumaLinkStudio.icns")
    write_ico(ROOT / "assets" / "LumaLinkStudio.ico", (16, 20, 24, 32, 40, 48, 64, 128, 256))
    tile(1024, 0.94).save(ROOT / "assets" / "LumaCam.png", optimize=True)
    # The header shows it at 32 px, sharp on high-density screens; browsers
    # show the tab icon at 16 to 32 px, so it takes the small mark.
    tile(192, 1.0).save(SITE_ASSETS / "icon.png", optimize=True)
    tile(64, 1.0, small=True).save(SITE_ASSETS / "favicon.png", optimize=True)
    write_android()
    print("Wrote the macOS, Windows, window, site and Android icons")


if __name__ == "__main__":
    main()
