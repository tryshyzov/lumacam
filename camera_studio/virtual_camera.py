import queue
import threading
import time
from collections.abc import Callable

import numpy as np
from PIL import Image

from camera_studio.camera_backend import VirtualCameraSession

OUTPUT_WIDTH = 1280
OUTPUT_HEIGHT = 720
OUTPUT_FPS = 30


class VirtualCameraOutput:
    def __init__(
        self,
        on_status: Callable[[str, bool], None],
        camera_factory: Callable[[], object] | None = None,
    ) -> None:
        self._on_status = on_status
        self._camera_factory = camera_factory
        self._frames: queue.Queue[Image.Image] = queue.Queue(maxsize=1)
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

    def submit(self, image: Image.Image) -> None:
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
                frame = np.zeros((OUTPUT_HEIGHT, OUTPUT_WIDTH, 3), dtype=np.uint8)
                last_frame_at = 0.0
                while not self._stop.is_set():
                    try:
                        image = self._frames.get(timeout=1 / OUTPUT_FPS)
                        while True:
                            try:
                                image = self._frames.get_nowait()
                            except queue.Empty:
                                break
                        image = image.convert("RGB")
                        frame = _to_output_frame(image)
                        last_frame_at = time.monotonic()
                    except queue.Empty:
                        if last_frame_at and time.monotonic() - last_frame_at > FRAME_TIMEOUT:
                            frame.fill(0)
                            last_frame_at = 0.0
                    camera.send(frame)
                    self._stop.wait(1 / OUTPUT_FPS)
        except Exception as error:
            if not self._stop.is_set():
                self._on_status(
                    "Не удалось запустить виртуальную камеру. "
                    f"Установите OBS Virtual Camera или Unity Capture: {error}",
                    True,
                )
        if self._stop.is_set():
            self._on_status("Виртуальная камера остановлена", False)


def _to_output_frame(image: Image.Image) -> np.ndarray:
    image = image.convert("RGB")
    image.thumbnail((OUTPUT_WIDTH, OUTPUT_HEIGHT), Image.Resampling.BILINEAR)
    canvas = Image.new("RGB", (OUTPUT_WIDTH, OUTPUT_HEIGHT))
    canvas.paste(
        image,
        ((OUTPUT_WIDTH - image.width) // 2, (OUTPUT_HEIGHT - image.height) // 2),
    )
    return np.array(canvas, dtype=np.uint8, copy=True, order="C")


FRAME_TIMEOUT = 1.0
