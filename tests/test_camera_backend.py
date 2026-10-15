import unittest
from unittest import mock

import numpy as np

from camera_studio import camera_backend
from camera_studio.camera_backend import VirtualCameraSession


class FakeBackend:
    device = "Fake camera backend"

    def __init__(self) -> None:
        self.frames: list[np.ndarray] = []
        self.closed = False

    def send(self, frame: np.ndarray) -> None:
        self.frames.append(frame)

    def close(self) -> None:
        self.closed = True


class VirtualCameraSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(camera_backend, "MACOS", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_prefers_obs_virtual_camera(self) -> None:
        calls: list[str] = []
        camera = FakeBackend()

        def factory(**options: object) -> FakeBackend:
            backend = str(options["backend"])
            calls.append(backend)
            return camera

        with VirtualCameraSession(factory) as session:
            self.assertEqual(session.device, "Fake camera backend")
            self.assertEqual(calls, ["obs"])
            frame = np.zeros((2, 2, 3), dtype=np.uint8)
            session.send(frame)
            self.assertIs(camera.frames[0], frame)

        self.assertTrue(camera.closed)

    def test_falls_back_to_unity_capture_if_obs_is_unavailable(self) -> None:
        calls: list[str] = []
        camera = FakeBackend()

        def factory(**options: object) -> FakeBackend:
            backend = str(options["backend"])
            calls.append(backend)
            if backend == "obs":
                raise RuntimeError("OBS Virtual Camera is not installed")
            return camera

        with VirtualCameraSession(factory) as session:
            self.assertEqual(session.device, "Fake camera backend")
        self.assertEqual(calls, ["obs", "unitycapture"])
        self.assertTrue(camera.closed)

    def test_reports_setup_help_when_no_backend_is_available(self) -> None:
        def factory(**options: object) -> FakeBackend:
            raise RuntimeError(f"{options['backend']} is unavailable")

        with self.assertRaisesRegex(
            RuntimeError,
            "OBS Studio.*Unity Capture",
        ) as error:
            VirtualCameraSession(factory).__enter__()

        self.assertIn("obs is unavailable", str(error.exception))
        self.assertIn("unitycapture is unavailable", str(error.exception))



class MacVirtualCameraSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(camera_backend, "MACOS", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_uses_only_obs(self) -> None:
        calls: list[str] = []

        def factory(**options: object) -> FakeBackend:
            calls.append(str(options["backend"]))
            raise RuntimeError("OBS Virtual Camera is not installed")

        with self.assertRaisesRegex(RuntimeError, "macOS.*OBS Studio 30") as error:
            VirtualCameraSession(factory).__enter__()

        self.assertEqual(calls, ["obs"])
        self.assertIn("OBS Virtual Camera is not installed", str(error.exception))
        self.assertNotIn("Unity", str(error.exception))


if __name__ == "__main__":
    unittest.main()
