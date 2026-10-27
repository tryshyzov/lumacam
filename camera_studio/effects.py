"""Background effects and face framing, for the preview and the virtual camera.

Two small MediaPipe models (assets/models/README.md) run with onnxruntime:
one tells the person from the background, for blurring the background or
putting a picture behind the person; the other finds the face, for keeping
it in frame by zooming in a little and following it. The effects work on a
picture of at most 1280×720, the virtual camera's size, which keeps them at
a few milliseconds a frame.

Frames arrive upside down, as the phone sends them (see
stream.correct_stream_orientation); the models need the person upright, so
the picture is turned for them and back.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

MODELS = Path(__file__).resolve().parents[1] / "assets" / "models"
MAX_SIDE = 1280
SEGMENTER_SIZE = 256
DETECTOR_SIZE = 128
DETECT_EVERY = 3  # frames
FACE_SCORE = 0.5
# Framing: how much of the frame's height the face should take, how far
# the frame may zoom in, how quickly it follows (per frame), and how long
# it holds on after the face is lost.
FACE_SHARE = 0.32
MAX_ZOOM = 2.2
FOLLOW = 0.08
HOLD_SECONDS = 1.5
MASK_SMOOTHING = 0.6  # of the new mask, against flicker at the edges


@dataclass(frozen=True)
class EffectSettings:
    background: str = "none"  # "none", "blur" or "image"
    blur: float = 0.6  # 0 … 1
    image: str | None = None  # the picture behind the person
    framing: bool = False

    @property
    def active(self) -> bool:
        background = self.background == "blur" or (self.background == "image" and bool(self.image))
        return self.framing or background


def face_anchors() -> np.ndarray:
    """BlazeFace short range's 896 anchor centres, as (x, y) in 0 … 1.

    Its SSD anchors: on a 16×16 grid (stride 8) two per cell, on an 8×8 grid
    (strides 16, three layers) six per cell, each at the cell's centre.
    """
    anchors = []
    for grid, per_cell in ((16, 2), (8, 6)):
        for y in range(grid):
            for x in range(grid):
                anchors += [((x + 0.5) / grid, (y + 0.5) / grid)] * per_cell
    return np.array(anchors, dtype=np.float32)


def decode_faces(regressors: np.ndarray, scores: np.ndarray, anchors: np.ndarray) -> list[tuple[float, float, float, float, float]]:
    """Faces as (score, centre x, centre y, width, height), in 0 … 1 of the input, best first."""
    score = 1 / (1 + np.exp(-np.clip(scores.reshape(-1), -50, 50)))
    keep = np.flatnonzero(score > FACE_SCORE)
    faces = []
    for index in keep[np.argsort(-score[keep])]:
        raw = regressors.reshape(-1, 16)[index]
        cx = raw[0] / DETECTOR_SIZE + anchors[index, 0]
        cy = raw[1] / DETECTOR_SIZE + anchors[index, 1]
        faces.append((float(score[index]), float(cx), float(cy), float(raw[2] / DETECTOR_SIZE), float(raw[3] / DETECTOR_SIZE)))
    return faces


def framing_box(face: tuple[float, float, float, float] | None, width: int, height: int) -> tuple[float, float, float, float]:
    """The part of the picture to show, (left, top, width, height), for a face
    (centre x, centre y, width, height in pixels) or the whole picture."""
    if face is None:
        return 0.0, 0.0, float(width), float(height)
    cx, cy, _face_width, face_height = face
    crop_height = min(float(height), max(height / MAX_ZOOM, face_height / FACE_SHARE))
    crop_width = crop_height * width / height
    # The face a little above the middle, as in a portrait.
    left = min(max(cx - crop_width / 2, 0.0), width - crop_width)
    top = min(max(cy - crop_height * 0.42, 0.0), height - crop_height)
    return left, top, crop_width, crop_height


class Effects:
    """Applies the current settings to decoded frames, on the decoding thread."""

    def __init__(self) -> None:
        self._settings = EffectSettings()
        self._segmenter = None
        self._detector = None
        self._anchors = face_anchors()
        self._mask: np.ndarray | None = None
        self._backgrounds: dict[tuple[str, int, int], np.ndarray] = {}
        self._count = 0
        self._box: tuple[float, float, float, float] | None = None
        self._face: tuple[float, float, float, float] | None = None
        self._face_seen = 0.0
        self._size: tuple[int, int] | None = None

    def configure(self, settings: EffectSettings) -> None:
        self._settings = settings

    def shown_part(self) -> tuple[float, float, float, float]:
        """The part of the phone's picture on screen now, as (left, top, width,
        height) in 0 … 1 of the upright picture: less than all of it while
        framing zooms in on a face."""
        box, size = self._box, self._size
        if box is None or size is None or not self._settings.framing:
            return 0.0, 0.0, 1.0, 1.0
        width, height = size
        left, top, crop_width, crop_height = box
        return left / width, top / height, crop_width / width, crop_height / height

    @property
    def settings(self) -> EffectSettings:
        return self._settings

    def apply(self, frame):
        """``frame`` with the effects, as a new rgb24 frame; the same frame without any.

        The picture stays as the phone sends it, upside down: only the small
        copies the models look at are turned upright, which saves turning
        the whole picture twice.
        """
        import av

        settings = self._settings
        if not settings.active:
            self._mask = None
            self._box = None
            return frame
        scale = min(1.0, MAX_SIDE / max(frame.width, frame.height))
        width = max(2, round(frame.width * scale) & ~1)
        height = max(2, round(frame.height * scale) & ~1)
        picture = frame.reformat(width=width, height=height, format="rgb24").to_image()
        self._count += 1
        if settings.framing:
            picture = self._frame(picture)
        else:
            self._box = None
        if settings.background in ("blur", "image"):
            picture = self._background(picture, settings)
        return av.VideoFrame.from_image(picture)

    # ------------------------------------------------------------ background

    def _session(self, name: str):
        import onnxruntime

        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = 2
        options.log_severity_level = 3
        return onnxruntime.InferenceSession(str(MODELS / name), options, providers=["CPUExecutionProvider"])

    def person_mask(self, picture: Image.Image) -> Image.Image:
        """The chance of a person at each pixel of the (upside down) picture, as an "L" image."""
        if self._segmenter is None:
            self._segmenter = self._session("selfie_segmenter.onnx")
        small = picture.resize((SEGMENTER_SIZE, SEGMENTER_SIZE), Image.Resampling.BILINEAR)
        upright = small.transpose(Image.Transpose.ROTATE_180)
        tensor = np.asarray(upright, dtype=np.float32)[None] / 255
        (mask,) = self._segmenter.run(None, {"input_1": tensor})
        mask = mask[0, :, :, 0]
        if self._mask is not None and self._mask.shape == mask.shape:
            mask = self._mask + (mask - self._mask) * MASK_SMOOTHING
        self._mask = mask
        image = Image.fromarray((np.clip(mask, 0, 1) * 255).astype(np.uint8), "L")
        return image.transpose(Image.Transpose.ROTATE_180).resize(picture.size, Image.Resampling.BILINEAR)

    def _background(self, picture: Image.Image, settings: EffectSettings) -> Image.Image:
        width, height = picture.size
        mask = self.person_mask(picture)
        if settings.background == "image" and settings.image:
            behind = self._picture(settings.image, width, height)
        else:
            # Blur a small copy: as soft as a big blur, at a fraction of its cost.
            small = picture.resize((max(1, width // 8), max(1, height // 8)), Image.Resampling.BILINEAR)
            radius = 0.6 + settings.blur * 3.4
            behind = small.filter(ImageFilter.GaussianBlur(radius)).resize((width, height), Image.Resampling.BILINEAR)
        return Image.composite(picture, behind, mask)

    def _picture(self, path: str, width: int, height: int) -> Image.Image:
        """``path`` filling the picture, cut to its proportions, upside down like the stream."""
        key = (path, width, height)
        cached = self._backgrounds.get(key)
        if cached is not None:
            return cached
        image = Image.open(path).convert("RGB")
        scale = max(width / image.width, height / image.height)
        size = (max(width, round(image.width * scale)), max(height, round(image.height * scale)))
        image = image.resize(size, Image.Resampling.LANCZOS)
        left, top = (size[0] - width) // 2, (size[1] - height) // 2
        picture = image.crop((left, top, left + width, top + height)).transpose(Image.Transpose.ROTATE_180)
        if len(self._backgrounds) > 4:
            self._backgrounds.clear()
        self._backgrounds[key] = picture
        return picture

    # --------------------------------------------------------------- framing

    def find_face(self, picture: Image.Image) -> tuple[float, float, float, float] | None:
        """The most likely face, as (centre x, centre y, width, height) in pixels of the upright picture."""
        if self._detector is None:
            self._detector = self._session("face_detector.onnx")
        width, height = picture.size
        side = max(width, height)
        # The detector takes a square: letterbox the picture into it.
        scale = DETECTOR_SIZE / side
        small = picture.resize((max(1, round(width * scale)), max(1, round(height * scale))), Image.Resampling.BILINEAR)
        square = Image.new("RGB", (DETECTOR_SIZE, DETECTOR_SIZE))
        offset = ((DETECTOR_SIZE - small.width) // 2, (DETECTOR_SIZE - small.height) // 2)
        square.paste(small.transpose(Image.Transpose.ROTATE_180), offset)
        tensor = np.asarray(square, dtype=np.float32)[None] / 127.5 - 1
        regressors, scores = self._detector.run(None, {"input": tensor})
        faces = decode_faces(regressors, scores, self._anchors)
        if not faces:
            return None
        _score, cx, cy, face_width, face_height = faces[0]
        return (
            (cx * DETECTOR_SIZE - offset[0]) / scale,
            (cy * DETECTOR_SIZE - offset[1]) / scale,
            face_width * DETECTOR_SIZE / scale,
            face_height * DETECTOR_SIZE / scale,
        )

    def _frame(self, picture: Image.Image) -> Image.Image:
        width, height = picture.size
        self._size = (width, height)
        now = time.monotonic()
        if self._count % DETECT_EVERY == 1 or self._face is None:
            face = self.find_face(picture)
            if face is not None:
                self._face, self._face_seen = face, now
            elif now - self._face_seen > HOLD_SECONDS:
                self._face = None
        target = framing_box(self._face, width, height)
        box = self._box if self._box is not None else (0.0, 0.0, float(width), float(height))
        box = tuple(current + (wanted - current) * FOLLOW for current, wanted in zip(box, target))
        self._box = box
        left, top, crop_width, crop_height = box
        if crop_width >= width - 1 and crop_height >= height - 1:
            return picture
        # The box is in the upright picture; this one is upside down.
        right, bottom = width - left, height - top
        crop = (int(right - crop_width), int(bottom - crop_height), int(math.ceil(right)), int(math.ceil(bottom)))
        return picture.crop(crop).resize((width, height), Image.Resampling.BILINEAR)
