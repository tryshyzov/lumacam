"""Looks: colour correction of the camera picture, for the preview and the virtual camera.

A look is a .cube LUT the user picked (as from a photo or video editor),
followed by brightness, contrast, saturation and warmth. All of it is baked
into one 3D LUT, which FFmpeg's lut3d filter applies to each decoded frame
in about 3 ms at 1080p; a neutral look leaves the frames untouched.
"""

from __future__ import annotations

import fractions
import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

CUBE_SIZE = 33


@dataclass(frozen=True)
class Look:
    """What to do to the colours; every value at 0 and no LUT is neutral."""

    lut: str | None = None  # path to a .cube file
    brightness: float = 0.0  # -1 … 1
    contrast: float = 0.0  # -1 … 1
    saturation: float = 0.0  # -1 (grey) … 1
    warmth: float = 0.0  # -1 (cooler, bluer) … 1 (warmer, more orange)

    @property
    def neutral(self) -> bool:
        return self == Look(lut=None)


# Built-in looks; the user's own LUT and the sliders go on top.
PRESETS: dict[str, Look] = {
    "none": Look(),
    "warm": Look(warmth=0.35, saturation=0.1),
    "cool": Look(warmth=-0.35, contrast=0.05),
    "vivid": Look(saturation=0.35, contrast=0.15),
    "film": Look(contrast=0.2, saturation=-0.2, warmth=0.15),
    "mono": Look(saturation=-1.0, contrast=0.15),
}


def read_cube(path: str | os.PathLike[str]) -> np.ndarray:
    """A .cube file's 3D LUT as an array indexed [blue, green, red, channel].

    The format lists output colours with red changing fastest, so reading
    the lines in order and reshaping gives that indexing.
    """
    size = 0
    domain_min = np.zeros(3)
    domain_max = np.ones(3)
    values: list[list[float]] = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, rest = line.partition(" ")
        if key == "LUT_3D_SIZE":
            size = int(rest)
        elif key == "DOMAIN_MIN":
            domain_min = np.array([float(v) for v in rest.split()])
        elif key == "DOMAIN_MAX":
            domain_max = np.array([float(v) for v in rest.split()])
        elif key in ("TITLE", "LUT_1D_SIZE", "LUT_1D_INPUT_RANGE", "LUT_3D_INPUT_RANGE"):
            if key == "LUT_1D_SIZE":
                raise ValueError("Это одномерная таблица (1D LUT); нужна трёхмерная (3D LUT)")
            continue
        else:
            parts = line.split()
            if len(parts) == 3:
                values.append([float(v) for v in parts])
    if size < 2 or len(values) != size**3:
        raise ValueError("Файл не похож на 3D LUT в формате .cube")
    table = np.array(values, dtype=np.float64).reshape(size, size, size, 3)
    return (table - domain_min) / np.where(domain_max > domain_min, domain_max - domain_min, 1)


def _sample(table: np.ndarray, rgb: np.ndarray) -> np.ndarray:
    """Look ``rgb`` (… × 3, in 0 … 1) up in ``table`` with trilinear interpolation."""
    size = table.shape[0]
    position = np.clip(rgb, 0, 1) * (size - 1)
    low = np.floor(position).astype(int)
    high = np.minimum(low + 1, size - 1)
    fraction = position - low
    r0, g0, b0 = low[..., 0], low[..., 1], low[..., 2]
    r1, g1, b1 = high[..., 0], high[..., 1], high[..., 2]
    fr, fg, fb = (fraction[..., i : i + 1] for i in range(3))

    def at(b, g, r):
        return table[b, g, r]

    c00 = at(b0, g0, r0) * (1 - fr) + at(b0, g0, r1) * fr
    c01 = at(b0, g1, r0) * (1 - fr) + at(b0, g1, r1) * fr
    c10 = at(b1, g0, r0) * (1 - fr) + at(b1, g0, r1) * fr
    c11 = at(b1, g1, r0) * (1 - fr) + at(b1, g1, r1) * fr
    c0 = c00 * (1 - fg) + c01 * fg
    c1 = c10 * (1 - fg) + c11 * fg
    return c0 * (1 - fb) + c1 * fb


def apply_adjustments(rgb: np.ndarray, look: Look) -> np.ndarray:
    """Brightness, contrast, saturation and warmth on colours in 0 … 1."""
    out = rgb + look.brightness * 0.25
    out = (out - 0.5) * (1 + look.contrast) + 0.5
    luma = out @ np.array([0.2126, 0.7152, 0.0722])
    out = luma[..., None] + (out - luma[..., None]) * (1 + look.saturation)
    out = out + look.warmth * np.array([0.08, 0.0, -0.08])
    return np.clip(out, 0, 1)


def bake(look: Look, size: int = CUBE_SIZE) -> np.ndarray:
    """The whole look as one 3D LUT, indexed [blue, green, red, channel]."""
    steps = np.linspace(0, 1, size)
    blue, green, red = np.meshgrid(steps, steps, steps, indexing="ij")
    rgb = np.stack([red, green, blue], axis=-1)
    if look.lut:
        rgb = _sample(read_cube(look.lut), rgb)
    return apply_adjustments(rgb, look)


def write_cube(table: np.ndarray, path: str | os.PathLike[str]) -> None:
    size = table.shape[0]
    lines = [f"LUT_3D_SIZE {size}"]
    lines += [f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in table.reshape(-1, 3)]
    Path(path).write_text("\n".join(lines) + "\n", encoding="ascii")


def combined(preset: str, own: Look) -> Look:
    """A preset with the user's own LUT and sliders on top of it."""
    base = PRESETS.get(preset, PRESETS["none"])
    return replace(
        base,
        lut=own.lut or base.lut,
        brightness=base.brightness + own.brightness,
        contrast=base.contrast + own.contrast,
        saturation=max(-1.0, base.saturation + own.saturation),
        warmth=base.warmth + own.warmth,
    )


def filter_path(path: Path) -> str:
    """``path`` as an FFmpeg filter option, whose ':' separates options
    (Windows paths start with one, as in C:/Users)."""
    return path.as_posix().replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


class LookFilter:
    """Applies a look to decoded frames, rebuilding FFmpeg's filter as needed.

    Used on one thread (the network thread decodes); ``set_look`` may be
    called from another, and the next frame picks the new look up.
    """

    def __init__(self) -> None:
        self._look = Look()
        self._wanted = Look()
        self._graph = None
        self._shape: tuple[int, int, str] | None = None
        self._folder = tempfile.mkdtemp(prefix="lumalink-look-")
        self._cube = Path(self._folder) / "look.cube"
        self._count = 0

    def set_look(self, look: Look) -> None:
        self._wanted = look

    def apply(self, frame):
        """``frame`` with the look, as a new frame; the same frame when neutral."""
        wanted = self._wanted
        if wanted.neutral:
            return frame
        shape = (frame.width, frame.height, frame.format.name)
        if self._graph is None or wanted != self._look or shape != self._shape:
            self._build(wanted, shape)
        # Frames from the decoder may lack timestamps, or restart with a new
        # connection; the filter only needs them to keep rising.
        frame.pts = self._count
        self._count += 1
        self._graph.push(frame)
        return self._graph.pull()

    def _build(self, look: Look, shape: tuple[int, int, str]) -> None:
        from av.filter import Graph

        write_cube(bake(look), self._cube)
        width, height, pixel_format = shape
        graph = Graph()
        source = graph.add_buffer(
            width=width, height=height, format=pixel_format, time_base=fractions.Fraction(1, 30)
        )
        lut = graph.add("lut3d", f"file={filter_path(self._cube)}:interp=tetrahedral")
        sink = graph.add("buffersink")
        source.link_to(lut)
        lut.link_to(sink)
        graph.configure()
        self._graph, self._look, self._shape = graph, look, shape
