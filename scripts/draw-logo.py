"""Draws the LumaLink logo's source files in assets/logo/ from its geometry.

The mark is a camera aperture: a light disc cut into six blades around a
hexagonal opening in the accent colour. Below about 48 px a sturdier
version is used (wider gaps, larger opening), since the fine gaps vanish.
The wordmark is "lumalink" in Sora SemiBold, its i dotted with the
mark's hexagon; the letters are converted to outlines, so the SVGs need no
font.

Every SVG uses plain paths only (no masks or clip paths), so Qt's SVG
renderer, which scripts/make-icons.py and the app use, draws them as
designed. Needs `pip install shapely fonttools` and Sora's variable font
(https://github.com/google/fonts/tree/main/ofl/sora, SIL Open Font
License):

    python scripts/draw-logo.py path/to/Sora[wght].ttf
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "logo"

CANVAS = 1024
TILE = "#232323"  # the icon's background
LIGHT = "#f4f4f2"  # the blades, and text on dark grounds
INK = "#222222"  # text on light grounds
ACCENT = "#ff5a3c"  # the opening, and the dot over the i
CORNER = 0.23  # tile corner radius, as a share of its side (flat icons only)

# The mark on a 100-unit square: disc radius, opening radius, gap width.
DISC = 31.0
FULL = {"opening": 12.0, "gap": 3.5}
SMALL = {"opening": 14.0, "gap": 6.0}


def hexagon(radius: float, center: tuple[float, float] = (50.0, 50.0)) -> list[tuple[float, float]]:
    cx, cy = center
    return [
        (cx + radius * math.cos(math.radians(60 * k - 90)), cy + radius * math.sin(math.radians(60 * k - 90)))
        for k in range(6)
    ]


def blades(opening: float, gap: float):
    """The disc cut into six blades: each gap runs from a corner of the
    opening outwards, along the extension of the opening's previous edge."""
    corners = hexagon(opening)
    cuts = [Polygon(corners)]
    for k in range(6):
        x, y = corners[k]
        nx, ny = corners[(k + 1) % 6]
        dx, dy = x - nx, y - ny
        length = math.hypot(dx, dy)
        far = (x + dx / length * 60, y + dy / length * 60)
        cuts.append(LineString([(x, y), far]).buffer(gap / 2, cap_style="flat"))
    disc = Point(50, 50).buffer(DISC, quad_segs=64)
    return disc.difference(unary_union(cuts))


def path(points, scale: float, dx: float = 0.0, dy: float = 0.0) -> str:
    pairs = [f"{x * scale + dx:.1f} {y * scale + dy:.1f}" for x, y in points]
    return "M" + " L".join(pairs) + " Z"


def shape_paths(geometry, scale: float, dx: float = 0.0, dy: float = 0.0) -> str:
    parts = getattr(geometry, "geoms", [geometry])
    return " ".join(path(list(part.exterior.coords)[:-1], scale, dx, dy) for part in parts)


def mark_parts(size: dict, scale: float, dx: float = 0.0, dy: float = 0.0) -> tuple[str, str]:
    """SVG path data for the blades and for the opening."""
    return (
        shape_paths(blades(size["opening"], size["gap"]), scale, dx, dy),
        path(hexagon(size["opening"]), scale, dx, dy),
    )


def tile_path(side: float, dx: float = 0.0, dy: float = 0.0) -> str:
    r = side * CORNER
    return (
        f"M{dx + r:.1f} {dy:.1f} H{dx + side - r:.1f} A{r:.1f} {r:.1f} 0 0 1 {dx + side:.1f} {dy + r:.1f} "
        f"V{dy + side - r:.1f} A{r:.1f} {r:.1f} 0 0 1 {dx + side - r:.1f} {dy + side:.1f} "
        f"H{dx + r:.1f} A{r:.1f} {r:.1f} 0 0 1 {dx:.1f} {dy + side - r:.1f} "
        f"V{dy + r:.1f} A{r:.1f} {r:.1f} 0 0 1 {dx + r:.1f} {dy:.1f} Z"
    )


def svg(width: float, height: float, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:g}" height="{height:g}" '
        f'viewBox="0 0 {width:g} {height:g}">\n{body}\n</svg>\n'
    )


def icon(size: dict) -> str:
    scale = CANVAS / 100
    blade, opening = mark_parts(size, scale)
    return svg(
        CANVAS,
        CANVAS,
        f'<path fill="{TILE}" d="{tile_path(CANVAS)}"/>\n'
        f'<path fill="{LIGHT}" d="{blade}"/>\n'
        f'<path fill="{ACCENT}" d="{opening}"/>',
    )


def layer(data: str, color: str) -> str:
    return svg(CANVAS, CANVAS, f'<path fill="{color}" d="{data}"/>')


class Wordmark:
    """ "lumalink" set in Sora SemiBold, its i dotted with the mark's hexagon."""

    TEXT = ["l", "u", "m", "a", "l", "dotlessi", "n", "k"]
    TRACKING = -0.03  # em

    def __init__(self, font_path: Path) -> None:
        font = TTFont(font_path)
        self.font = instantiateVariableFont(font, {"wght": 600})
        self.glyphs = self.font.getGlyphSet()
        self.em = self.font["head"].unitsPerEm
        cmap = self.font.getBestCmap()
        self.glyph_i = cmap[ord("i")]
        self.names = [cmap[ord(c)] if len(c) == 1 else c for c in self.TEXT]

    def _bounds(self, name: str) -> tuple[int, int, int, int]:
        glyph = self.font["glyf"][name]
        glyph.recalcBounds(self.font["glyf"])
        return glyph.xMin, glyph.yMin, glyph.xMax, glyph.yMax

    def draw(self, size: float, x: float, baseline: float) -> tuple[str, tuple[float, float, float], float]:
        """Path data for the letters at font `size`, the dot's centre and
        radius, and the text's width."""
        scale = size / self.em
        pen = SVGPathPen(self.glyphs)
        cursor = 0.0
        dot = None
        for name in self.names:
            transform = (scale, 0, 0, -scale, x + cursor * scale, baseline)
            self.glyphs[name].draw(TransformPen(pen, transform))
            if name == "dotlessi":
                # Where the i's own dot sits: above the dotless i's stem.
                _, _, _, stem_top = self._bounds(name)
                left, _, right, top = self._bounds(self.glyph_i)
                center_x = x + (cursor + (left + right) / 2) * scale
                center_y = baseline - (stem_top + top) / 2 * scale
                radius = (top - stem_top) * scale * 0.62
                dot = (center_x, center_y, radius)
            cursor += self.glyphs[name].width + self.TRACKING * self.em
        width = (cursor - self.TRACKING * self.em) * scale
        return pen.getCommands(), dot, width

    def ascender(self, size: float) -> float:
        return self._bounds(self.names[0])[3] * size / self.em


def lockup(wordmark: Wordmark, text_color: str) -> str:
    """The mark followed by the wordmark, as in the logo board."""
    mark = 256.0
    size = mark * 0.72
    gap = mark * 0.28
    blade, opening = mark_parts(FULL, mark / 100, 0, 0)
    ascender = wordmark.ascender(size)
    baseline = mark / 2 + ascender / 2
    letters, (dot_x, dot_y, dot_r), text_width = wordmark.draw(size, mark + gap, baseline)
    dot = path(hexagon(dot_r, (dot_x, dot_y)), 1.0)
    width = mark + gap + text_width
    return svg(
        round(width + 1),
        mark,
        f'<path fill="{TILE}" d="{tile_path(mark)}"/>\n'
        f'<path fill="{LIGHT}" d="{blade}"/>\n'
        f'<path fill="{ACCENT}" d="{opening}"/>\n'
        f'<path fill="{text_color}" d="{letters}"/>\n'
        f'<path fill="{ACCENT}" d="{dot}"/>',
    )


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    wordmark = Wordmark(Path(sys.argv[1]))
    layers = OUT / "layers"
    layers.mkdir(parents=True, exist_ok=True)
    scale = CANVAS / 100
    files = {
        OUT / "logo-icon.svg": icon(FULL),
        OUT / "logo-icon-small.svg": icon(SMALL),
        OUT / "logo-wordmark-dark.svg": lockup(wordmark, LIGHT),
        OUT / "logo-wordmark-light.svg": lockup(wordmark, INK),
    }
    for name, size in (("", FULL), ("-small", SMALL)):
        blade, opening = mark_parts(size, scale)
        files[layers / f"blades{name}.svg"] = layer(blade, LIGHT)
        files[layers / f"opening{name}.svg"] = layer(opening, ACCENT)
    for target, text in files.items():
        target.write_text(text, encoding="utf-8", newline="\n")
        print(target.relative_to(ROOT))


if __name__ == "__main__":
    main()
