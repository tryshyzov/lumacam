import threading
import time
import unittest

import numpy as np
from PIL import Image

from camera_studio.virtual_camera import (
    OUTPUT_FPS,
    OUTPUT_HEIGHT,
    OUTPUT_WIDTH,
    VirtualCameraOutput,
)


class FakeCamera:
    def __init__(self) -> None:
        self.frames: list[np.ndarray] = []
        self.frame_ready = threading.Event()
        self.closed = False

    @property
    def device(self) -> str:
        return "LumaLink Virtual Camera"

    def __enter__(self) -> "FakeCamera":
        return self

    def __exit__(self, *_args: object) -> None:
        self.closed = True

    def send(self, frame: np.ndarray) -> None:
        self.frames.append(frame.copy())
        self.frame_ready.set()

    def sleep_until_next_frame(self) -> None:
        time.sleep(0.002)


class VirtualCameraOutputTests(unittest.TestCase):
    def test_opens_backend_and_sends_fitted_rgb_frames(self) -> None:
        statuses: list[tuple[str, bool]] = []
        started = threading.Event()
        camera_holder: list[FakeCamera] = []

        def camera_factory() -> FakeCamera:
            camera = FakeCamera()
            camera_holder.append(camera)
            return camera

        def on_status(message: str, is_error: bool) -> None:
            statuses.append((message, is_error))
            if message.startswith("Виртуальная камера запущена"):
                started.set()

        output = VirtualCameraOutput(on_status, camera_factory)
        output.start()
        try:
            self.assertTrue(started.wait(2))
            camera = camera_holder[0]
            camera.frame_ready.clear()
            output.submit(Image.new("RGB", (4, 4), (255, 0, 0)))
            self.assertTrue(camera.frame_ready.wait(2))
            frame = next(
                frame for frame in camera.frames if tuple(frame[360, 640]) == (255, 0, 0)
            )
            self.assertEqual(frame.shape, (OUTPUT_HEIGHT, OUTPUT_WIDTH, 3))
            self.assertEqual(tuple(frame[10, 10]), (0, 0, 0))
            self.assertTrue(frame.flags.c_contiguous)
        finally:
            output.stop()

        self.assertTrue(camera_holder[0].closed)
        self.assertTrue(any("Виртуальная камера остановлена" in text for text, _ in statuses))

    def test_reports_backend_errors(self) -> None:
        statuses: list[tuple[str, bool]] = []
        failed = threading.Event()

        def camera_factory() -> object:
            raise RuntimeError("OBS Virtual Camera and Unity Capture are unavailable")

        def on_status(message: str, is_error: bool) -> None:
            statuses.append((message, is_error))
            if is_error:
                failed.set()

        output = VirtualCameraOutput(on_status, camera_factory)
        output.start()
        self.assertTrue(failed.wait(2))
        self.assertTrue(statuses[-1][1])
        self.assertIn("OBS Virtual Camera and Unity Capture are unavailable", statuses[-1][0])
        output.stop()


if __name__ == "__main__":
    unittest.main()
