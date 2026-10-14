"""Draws assets/LumaLinkStudio.icns, the macOS app icon, from LumaCam.svg.

macOS puts icons that do not follow its grid on a gray plate, so the logo
goes on the macOS icon shape: an 824 px rounded square with continuous
corners, centered on a 1024 px canvas, over a soft shadow. Needs macOS
(iconutil) and the project's dependencies:

    python3 scripts/make-macos-icon.py
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from math import cos, pi, sin
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter
from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "LumaCam.svg"
TARGET = ROOT / "assets" / "LumaLinkStudio.icns"

CANVAS = 1024
BODY = 824  # Apple's macOS icon grid
CORNER_EXPONENT = 5  # a superellipse close to the macOS continuous corner
LOGO_SCALE = 0.9  # keeps the record light and the wordmark off the corners
BACKGROUND = (48, 48, 48, 255)  # the logo's own #303030
SUPERSAMPLE = 4


def icon_shape(size: int) -> Image.Image:
    """A mask of the macOS icon shape, ``BODY`` wide, centered on ``size``."""
    half = BODY * size / CANVAS / 2
    center = size / 2
    points = []
    steps = 720
    for index in range(steps):
        angle = 2 * pi * index / steps
        c, s = cos(angle), sin(angle)
        x = half * abs(c) ** (2 / CORNER_EXPONENT) * (1 if c >= 0 else -1)
        y = half * abs(s) ** (2 / CORNER_EXPONENT) * (1 if s >= 0 else -1)
        points.append((center + x, center + y))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).polygon(points, fill=255)
    return mask


def logo(size: int) -> Image.Image:
    """The logo without its square background, scaled into the icon body."""
    svg = SOURCE.read_text(encoding="utf-8")
    # The first rect is the logo's square plate; the icon shape replaces it.
    svg = re.sub(r"<rect [^>]*/>", "", svg, count=1)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    image = QImage(size, size, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    side = BODY * size / CANVAS * LOGO_SCALE
    offset = (size - side) / 2
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(offset, offset, side, side))
    painter.end()
    return Image.frombytes("RGBA", (size, size), bytes(image.constBits()))


def master() -> Image.Image:
    size = CANVAS * SUPERSAMPLE
    shape = icon_shape(size)

    shadow_alpha = shape.point(lambda value: value * 0.3)
    shadow_alpha = ImageChops.offset(shadow_alpha, 0, 10 * SUPERSAMPLE)
    shadow_alpha = shadow_alpha.filter(ImageFilter.GaussianBlur(10 * SUPERSAMPLE))
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.putalpha(shadow_alpha)

    body = Image.new("RGBA", (size, size), BACKGROUND)
    body.alpha_composite(logo(size))
    canvas.paste(body, (0, 0), shape)
    return canvas.resize((CANVAS, CANVAS), Image.Resampling.LANCZOS)


def main() -> None:
    if sys.platform != "darwin":
        sys.exit("iconutil, which builds the .icns, is only on macOS")
    QGuiApplication.instance() or QGuiApplication([])
    image = master()
    with tempfile.TemporaryDirectory() as folder:
        iconset = Path(folder) / "LumaLinkStudio.iconset"
        iconset.mkdir()
        for points in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                pixels = points * scale
                name = f"icon_{points}x{points}{'@2x' if scale == 2 else ''}.png"
                image.resize((pixels, pixels), Image.Resampling.LANCZOS).save(iconset / name)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(TARGET)], check=True)
    print(f"Wrote {TARGET.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
