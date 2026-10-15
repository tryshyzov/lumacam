import threading
import time
import unittest

import av
import numpy as np
from PIL import Image

from camera_studio import virtual_camera
from camera_studio.stream import correct_stream_orientation
from camera_studio.virtual_camera import (
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


class OutputFrameTests(unittest.TestCase):
    """The FFmpeg scaler gives what Pillow's thumbnail on a black canvas gave."""

    @staticmethod
    def pillow_reference(image: Image.Image) -> np.ndarray:
        image = image.copy()
        image.thumbnail((OUTPUT_WIDTH, OUTPUT_HEIGHT), Image.Resampling.BILINEAR)
        canvas = Image.new("RGB", (OUTPUT_WIDTH, OUTPUT_HEIGHT))
        canvas.paste(image, ((OUTPUT_WIDTH - image.width) // 2, (OUTPUT_HEIGHT - image.height) // 2))
        return np.asarray(canvas)

    def test_decoded_frames_match_the_turned_picture(self) -> None:
        rng = np.random.default_rng(2)
        for size in ((1920, 1080), (1080, 1920), (640, 480), (1280, 720)):
            with self.subTest(size=size):
                small = rng.integers(0, 256, (9, 16, 3), dtype=np.uint8)
                image = Image.fromarray(small).resize(size, Image.Resampling.BICUBIC)
                # As the decoder hands it over: YUV, and upside down.
                decoded = av.VideoFrame.from_image(image).reformat(format="yuv420p")

                frame = virtual_camera._to_output_frame(decoded)
                expected = virtual_camera._to_output_frame(
                    correct_stream_orientation(decoded.to_image())
                )

                # A view in reverse order, which the camera outputs copy.
                self.assertEqual(frame.shape, (OUTPUT_HEIGHT, OUTPUT_WIDTH, 3))
                difference = np.abs(frame.astype(int) - expected.astype(int))
                self.assertLess(difference.mean(), 2)
                self.assertTrue(((frame == 0).all(axis=2) == (expected == 0).all(axis=2)).mean() > 0.999)

    def test_matches_pillow_for_every_shape(self) -> None:
        rng = np.random.default_rng(1)
        for size in ((1920, 1080), (1080, 1920), (1440, 1080), (640, 480), (1280, 720), (2560, 1440)):
            with self.subTest(size=size):
                # Smooth content, like a camera picture.
                small = rng.integers(0, 256, (9, 16, 3), dtype=np.uint8)
                image = Image.fromarray(small).resize(size, Image.Resampling.BICUBIC)

                frame = virtual_camera._to_output_frame(image)
                expected = self.pillow_reference(image)

                self.assertEqual(frame.shape, (OUTPUT_HEIGHT, OUTPUT_WIDTH, 3))
                self.assertEqual(frame.dtype, np.uint8)
                self.assertTrue(frame.flags.c_contiguous)
                difference = np.abs(frame.astype(int) - expected.astype(int))
                self.assertLess(difference.mean(), 1.5)
                # The black bars are exactly where they were.
                self.assertTrue(((frame == 0).all(axis=2) == (expected == 0).all(axis=2)).mean() > 0.999)

