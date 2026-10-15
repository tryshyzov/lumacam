import functools
import math
import queue
import threading
import time
from collections.abc import Callable

import av
import numpy as np
from PIL import Image, ImageDraw

from camera_studio.camera_backend import VirtualCameraSession
from camera_studio.stream import correct_stream_orientation

OUTPUT_WIDTH = 1280
OUTPUT_HEIGHT = 720
OUTPUT_FPS = 30
FRAME_TIMEOUT = 1.0
IDLE_BACKGROUND = 128
IDLE_GLYPH = 205


class VirtualCameraOutput:
    def __init__(
        self,
        on_status: Callable[[str, bool], None],
        camera_factory: Callable[[], object] | None = None,
    ) -> None:
        self._on_status = on_status
        self._camera_factory = camera_factory
        self._frames: queue.Queue[Image.Image | av.VideoFrame] = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="LumaLink virtual camera",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=2)

    def submit(self, image: Image.Image | av.VideoFrame) -> None:
        """Show a picture, or a frame straight from the H.264 decoder.

        A decoded frame is still upside down, as the phone sends it; the
        camera turns it with stream.correct_stream_orientation.
        """
        if self._stop.is_set():
            return
        try:
            self._frames.put_nowait(image)
        except queue.Full:
            try:
                self._frames.get_nowait()
            except queue.Empty:
                pass
            self._frames.put_nowait(image)

    def _run(self) -> None:
        try:
            camera_factory = self._camera_factory or VirtualCameraSession
            with camera_factory() as camera:
                device = getattr(camera, "device", "LumaLink Virtual Camera")
                self._on_status(f"Виртуальная камера запущена: {device}", False)
                frame = idle_frame()
                last_frame_at = 0.0
                while not self._stop.is_set():
                    try:
                        image = self._frames.get(timeout=1 / OUTPUT_FPS)
                        while True:
                            try:
                                image = self._frames.get_nowait()
                            except queue.Empty:
                                break
                        frame = _to_output_frame(image)
                        last_frame_at = time.monotonic()
                    except queue.Empty:
                        if last_frame_at and time.monotonic() - last_frame_at > FRAME_TIMEOUT:
                            frame = idle_frame()
                            last_frame_at = 0.0
                    # Paced by the frames themselves: waiting for the next one
                    # above already takes a frame interval at most.
                    camera.send(frame)
        except Exception as error:
            if not self._stop.is_set():
                self._on_status(
                    f"Не удалось запустить виртуальную камеру. {error}",
                    True,
                )
        if self._stop.is_set():
            self._on_status("Виртуальная камера остановлена", False)


def _to_output_frame(image: Image.Image | av.VideoFrame) -> np.ndarray:
    """The picture fitted into the camera's frame, centered on black.

    Shrinking a 1080p picture with Pillow took about 6 ms; FFmpeg's scaler,
    already here for decoding, does it in about 2. The size is the one
    Image.thumbnail picks, so a smaller picture is never enlarged.

    A decoded frame is converted from YUV and scaled in one pass and turned
    upright only at the camera's size: 1.4 ms for a 1080p frame, against
    2.6 ms through the full-size picture. Pillow turns it; NumPy's reversed
    copy took 2.3 ms by itself.
    """
    if isinstance(image, av.VideoFrame):
        width, height = _fitted_size(image.width, image.height)
        scaled = image.reformat(width=width, height=height, format="rgb24", interpolation="BILINEAR")
        picture = np.asarray(correct_stream_orientation(scaled.to_image()))
        if (width, height) == (OUTPUT_WIDTH, OUTPUT_HEIGHT):
            return picture
        return _centered(picture)
    image = image.convert("RGB")
    if image.size == (OUTPUT_WIDTH, OUTPUT_HEIGHT):
        return np.array(image, dtype=np.uint8, copy=True, order="C")
    width, height = _fitted_size(image.width, image.height)
    if (width, height) == image.size:
        picture = np.asarray(image)
    else:
        picture = (
            av.VideoFrame.from_image(image)
            .reformat(width=width, height=height, format="rgb24", interpolation="BILINEAR")
            .to_ndarray()
        )
    return _centered(picture)


def _centered(picture: np.ndarray) -> np.ndarray:
    """The picture in the middle of a black camera frame."""
    height, width = picture.shape[:2]
    frame = np.zeros((OUTPUT_HEIGHT, OUTPUT_WIDTH, 3), dtype=np.uint8)
    left = (OUTPUT_WIDTH - width) // 2
    top = (OUTPUT_HEIGHT - height) // 2
    frame[top : top + height, left : left + width] = picture
    return frame


def _fitted_size(width: int, height: int) -> tuple[int, int]:
    """The size Image.thumbnail((OUTPUT_WIDTH, OUTPUT_HEIGHT)) gives a picture."""
    if width <= OUTPUT_WIDTH and height <= OUTPUT_HEIGHT:
        return width, height
    aspect = width / height
    x, y = OUTPUT_WIDTH, OUTPUT_HEIGHT

    def round_aspect(number: float, key: Callable[[int], float]) -> int:
        return max(min(math.floor(number), math.ceil(number), key=key), 1)

    if x / y >= aspect:
        x = round_aspect(y * aspect, key=lambda n: abs(aspect - n / y))
    else:
        y = round_aspect(x / aspect, key=lambda n: 0 if n == 0 else abs(aspect - x / n))
    return x, y


@functools.lru_cache(maxsize=2)
def _idle_frame(width: int, height: int) -> np.ndarray:
    """The image shown while no phone is streaming: a camera glyph on gray.

    It matches the idle image the LumaLink Camera filter draws by itself when
    LumaLink Studio is not running (``FillIdleImage`` in ``virtualcam/``).
    """
    scale = 4
    image = Image.new("L", (width * scale, height * scale), IDLE_BACKGROUND)
    draw = ImageDraw.Draw(image)
    unit = height * scale
    center_x = width * scale / 2
    center_y = height * scale / 2
    draw.polygon(
        [
            (center_x - 0.131 * unit, center_y - 0.084 * unit),
            (center_x - 0.070 * unit, center_y - 0.045 * unit),
            (center_x - 0.070 * unit, center_y + 0.045 * unit),
            (center_x - 0.131 * unit, center_y + 0.084 * unit),
        ],
        fill=IDLE_GLYPH,
    )
    draw.rounded_rectangle(
        (
            center_x - 0.050 * unit,
            center_y - 0.0985 * unit,
            center_x + 0.131 * unit,
            center_y + 0.0985 * unit,
        ),
        radius=0.033 * unit,
        fill=IDLE_GLYPH,
    )
    gray = np.asarray(image.reduce(scale), dtype=np.uint8)
    frame = np.ascontiguousarray(np.repeat(gray[:, :, None], 3, axis=2))
    frame.setflags(write=False)
    return frame


def idle_frame(width: int = OUTPUT_WIDTH, height: int = OUTPUT_HEIGHT) -> np.ndarray:
    """The idle image; drawn once per size and shared, so it is read-only."""
    return _idle_frame(width, height)
