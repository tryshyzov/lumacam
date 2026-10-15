"""Draws every app icon from one design: the layers in assets/LumaLinkStudio.icon.

That Icon Composer icon is what macOS 26+ shows, in glass. This script flattens
the same layers (lens ring, lens, iris, pupil, record light) on the logo's
dark gray, so the other platforms get the same picture:

    assets/LumaLinkStudio.icns   macOS before 26, and builds without Xcode
    assets/LumaLinkStudio.ico    Windows executable and installer
    assets/LumaCam.png           the app window's icon and the README logo
    android-app/.../mipmap-*     Android adaptive icon, with a themed variant

Needs macOS (iconutil) and the project's dependencies:

    python3 scripts/make-icons.py
"""

from __future__ import annotations

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
ANDROID_RES = ROOT / "android-app" / "src" / "main" / "res"

# The layers' canvas, and where its parts are on it (see the .icon's SVGs).
CANVAS = 1024
LENS_LAYERS = ("body.svg", "lens.svg", "iris.svg", "pupil.svg")  # bottom first
RECORD_LAYERS = ("bezel.svg", "dot.svg")
LENS_CENTER = (512.0, 512.0)
LENS_RADIUS = 405.6
RECORD_CENTER = (883.6, 140.4)
RECORD_RADIUS = 47.1

BACKGROUND = (48, 48, 48, 255)  # the logo's #303030, the .icon's fill
CORNER_EXPONENT = 5  # a superellipse close to Apple's continuous corners
SUPERSAMPLE = 4

# Android adaptive icons: a 108 dp layer, of which launchers may show only the
# circle 66 dp across, so the record light moves in from the corner.
ANDROID_DENSITIES = {"mdpi": 1, "hdpi": 1.5, "xhdpi": 2, "xxhdpi": 3, "xxxhdpi": 4}
ANDROID_LENS_RADIUS = 0.255  # of the layer's width
ANDROID_RECORD_DISTANCE = 0.27


def render(layers: tuple[str, ...], size: int, center: tuple[float, float], scale: float,
           anchor: tuple[float, float]) -> Image.Image:
    """Draw layers with ``anchor`` (in layer pixels) at ``center``, ``scale`` times."""
    image = QImage(size, size, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    side = CANVAS * scale
    target = QRectF(center[0] - anchor[0] * scale, center[1] - anchor[1] * scale, side, side)
    for name in layers:
        QSvgRenderer(str(LAYERS / name)).render(painter, target)
    painter.end()
    return Image.frombytes("RGBA", (size, size), bytes(image.constBits()))


def artwork(size: int) -> Image.Image:
    """The layers as laid out on their own canvas, at ``size``."""
    scale = size / CANVAS
    middle = (size / 2, size / 2)
    image = render(LENS_LAYERS, size, middle, scale, (CANVAS / 2, CANVAS / 2))
    image.alpha_composite(render(RECORD_LAYERS, size, middle, scale, (CANVAS / 2, CANVAS / 2)))
    return image


def shape(size: int, body: float) -> Image.Image:
    """A mask of a rounded square ``body`` wide, centered on ``size``."""
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


def tile(size: int, body_fraction: float, shadow: bool) -> Image.Image:
    """The icon on its dark rounded square, the square ``body_fraction`` of ``size``."""
    big = size * SUPERSAMPLE
    body = round(big * body_fraction)
    mask = shape(big, body)
    canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    if shadow:
        alpha = mask.point(lambda value: value * 0.3)
        alpha = ImageChops.offset(alpha, 0, round(big * 10 / CANVAS))
        canvas.putalpha(alpha.filter(ImageFilter.GaussianBlur(big * 10 / CANVAS)))
    plate = Image.new("RGBA", (big, big), BACKGROUND)
    art = artwork(body)
    plate.alpha_composite(art, ((big - body) // 2, (big - body) // 2))
    canvas.paste(plate, (0, 0), mask)
    return canvas.resize((size, size), Image.Resampling.LANCZOS)


def android_layers(size: int) -> tuple[Image.Image, Image.Image]:
    """The adaptive icon's foreground and its one-color themed variant."""
    big = size * SUPERSAMPLE
    scale = ANDROID_LENS_RADIUS * big / LENS_RADIUS
    middle = (big / 2, big / 2)
    record = (middle[0] + ANDROID_RECORD_DISTANCE * big * cos(pi / 4),
              middle[1] - ANDROID_RECORD_DISTANCE * big * sin(pi / 4))
    foreground = render(LENS_LAYERS, big, middle, scale, LENS_CENTER)
    foreground.alpha_composite(render(RECORD_LAYERS, big, record, scale, RECORD_CENTER))

    # Themed icons take only the shape; draw the rings and the light as lines.
    mono = Image.new("L", (big, big), 0)
    draw = ImageDraw.Draw(mono)

    def ring(center: tuple[float, float], outer: float, inner: float) -> None:
        x, y = center
        draw.ellipse((x - outer, y - outer, x + outer, y + outer), fill=255)
        draw.ellipse((x - inner, y - inner, x + inner, y + inner), fill=0)

    ring(middle, 405.6 * scale, 309.1 * scale)
    ring(middle, 159.0 * scale, 114.4 * scale)
    # A gap around the light keeps it apart from the lens ring it overlaps.
    gap = RECORD_RADIUS * scale * 1.5
    draw.ellipse((record[0] - gap, record[1] - gap, record[0] + gap, record[1] + gap), fill=0)
    ring(record, RECORD_RADIUS * scale, 0)
    monochrome = Image.new("RGBA", (big, big), (255, 255, 255, 0))
    monochrome.putalpha(mono)
    return (
        foreground.resize((size, size), Image.Resampling.LANCZOS),
        monochrome.resize((size, size), Image.Resampling.LANCZOS),
    )


def write_icns(path: Path) -> None:
    master = tile(1024, 824 / 1024, shadow=True)  # Apple's macOS icon grid
    with tempfile.TemporaryDirectory() as folder:
        iconset = Path(folder) / "LumaLinkStudio.iconset"
        iconset.mkdir()
        for points in (16, 32, 128, 256, 512):
            for factor in (1, 2):
                pixels = points * factor
                name = f"icon_{points}x{points}{'@2x' if factor == 2 else ''}.png"
                master.resize((pixels, pixels), Image.Resampling.LANCZOS).save(iconset / name)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(path)], check=True)


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
    (values / "ic_launcher_background.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<resources>\n"
        '    <color name="ic_launcher_background">#303030</color>\n'
        "</resources>\n"
    )
    for density, factor in ANDROID_DENSITIES.items():
        folder = ANDROID_RES / f"mipmap-{density}"
        folder.mkdir(exist_ok=True)
        foreground, monochrome = android_layers(round(108 * factor))
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
    # Windows draws icons edge to edge; a small margin keeps the corners clean.
    windows = tile(256, 0.94, shadow=False)
    windows.save(ROOT / "assets" / "LumaLinkStudio.ico",
                 sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    tile(1024, 824 / 1024, shadow=True).save(ROOT / "assets" / "LumaCam.png", optimize=True)
    write_android()
    print("Wrote the macOS, Windows, window and Android icons")


if __name__ == "__main__":
    main()
