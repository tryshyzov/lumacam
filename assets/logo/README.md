# LumaLink logo

The mark is a camera aperture: a light disc cut into six blades around a
hexagonal opening in the accent colour. The wordmark is "lumalink" in Sora
SemiBold with the i dotted by the same hexagon, converted to outlines.
`scripts/draw-logo.py` draws every SVG here from that geometry; rerun it
after changing a colour or size there.

| File | Use |
|---|---|
| `logo-icon.svg`, `logo-icon.png` | The app icon, flat: tile, blades, opening (1024 × 1024) |
| `logo-icon-small.svg` | The same for 48 px and smaller: wider gaps, larger opening |
| `logo-wordmark-dark.svg`, `.png` | Mark and name on dark grounds (README, site) |
| `logo-wordmark-light.svg`, `.png` | Mark and name on light grounds |
| `layers/blades.svg` | The blades alone, light on transparent. Also the monochrome (Android themed) icon |
| `layers/opening.svg` | The opening alone, accent on transparent |
| `layers/*-small.svg` | The same two layers of the small version |

The layers share one 1024 canvas with the mark centred, as in an Icon
Composer icon: the background is the tile colour, the blades sit above it
and the opening on top.

| | Value |
|---|---|
| Tile | `#232323` |
| Blades, text on dark | `#f4f4f2` |
| Text on light | `#222222` |
| Accent (opening, dot) | `#ff5a3c` |
| Tile corner radius | 23 % of the side (flat icons; platforms apply their own mask) |
| Disc | radius 31 % of the side, centred |
| Opening | hexagon, radius 12 % (small: 14 %), a corner pointing up |
| Gaps | 3.5 % wide (small: 6 %) |

Every SVG is plain paths, with no masks or clip paths, so Qt's renderer
draws them as designed. Sora is under the SIL Open Font License; only its
outlines are used here.
