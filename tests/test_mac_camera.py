import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from camera_studio import camera_backend, mac_camera


def fake_helper(folder: str, script: str) -> Path:
    """A stand-in for ``lumalink-camera`` that runs the given shell script."""
    helper = Path(folder) / "lumalink-camera"
    helper.write_text(f"#!/bin/sh\n{script}\n")
    helper.chmod(helper.stat().st_mode | stat.S_IEXEC)
    return helper


@unittest.skipIf(sys.platform == "win32", "runs a shell script as the helper")
class MacCameraDeviceTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name

    def test_streams_raw_rgb_frames_to_the_helper(self) -> None:
        received = Path(self.folder) / "frames"
        helper = fake_helper(self.folder, f'[ "$1" = feed ] && cat > "{received}"')
        first = np.full((2, 3, 3), 7, dtype=np.uint8)
        second = np.arange(18, dtype=np.uint8).reshape(2, 3, 3)

        camera = mac_camera.MacCameraDevice(helper)
        camera.send(first)
        camera.send(second)
        camera.close()

        self.assertEqual(received.read_bytes(), first.tobytes() + second.tobytes())

    def test_reports_why_the_helper_stopped(self) -> None:
        helper = fake_helper(self.folder, 'echo "LumaLink Camera is not installed" >&2; exit 1')
        camera = mac_camera.MacCameraDevice(helper)
        self.addCleanup(camera.close)

        with self.assertRaisesRegex(RuntimeError, "not installed"):
            for _ in range(100):
                camera.send(np.zeros((720, 1280, 3), dtype=np.uint8))

    def test_installed_when_the_helper_finds_the_camera(self) -> None:
        for code, installed in ((0, True), (1, False)):
            with self.subTest(code=code):
                helper = fake_helper(self.folder, f'[ "$1" = status ] && exit {code}; exit 2')
                with (
                    mock.patch.object(mac_camera.sys, "platform", "darwin"),
                    mock.patch.object(mac_camera, "helper_path", return_value=helper),
                ):
                    self.assertEqual(mac_camera.is_installed(), installed)

    def test_not_installed_without_the_helper(self) -> None:
        missing = Path(self.folder) / "missing"
        with mock.patch.object(mac_camera, "helper_path", return_value=missing):
            self.assertFalse(mac_camera.is_installed())


class MacBackendChoiceTests(unittest.TestCase):
    def test_macos_prefers_its_own_camera_when_installed(self) -> None:
        own_camera = mock.Mock(device="LumaLink Camera")
        with (
            mock.patch.object(camera_backend, "MACOS", True),
            mock.patch.object(camera_backend.mac_camera, "is_installed", return_value=True),
            mock.patch.object(camera_backend.mac_camera, "MacCameraDevice", return_value=own_camera),
        ):
            with camera_backend.VirtualCameraSession() as session:
                self.assertEqual(session.device, "LumaLink Camera")

        own_camera.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
