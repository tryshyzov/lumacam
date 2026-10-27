import tempfile
import unittest
from pathlib import Path
from unittest import mock

from camera_studio import camera_backend, linux_camera
from camera_studio.camera_backend import VirtualCameraSession


def fake_sysfs(root: Path, devices: dict[str, tuple[str, bool]]) -> Path:
    """/sys/class/video4linux with name files; a real camera links to its USB device."""
    sysfs = root / "video4linux"
    for node, (name, physical) in devices.items():
        entry = sysfs / node
        entry.mkdir(parents=True)
        (entry / "name").write_text(name + "\n", encoding="utf-8")
        if physical:
            usb = root / "devices" / "pci0000:00" / node
            usb.mkdir(parents=True)
            (entry / "device").symlink_to(usb)
    return sysfs


class LoopbackDeviceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def test_prefers_lumalink_camera_and_skips_real_cameras(self) -> None:
        try:
            sysfs = fake_sysfs(self.root, {
                "video0": ("Integrated Webcam", True),
                "video2": ("OBS Virtual Camera", False),
                "video10": ("LumaLink Camera", False),
            })
        except OSError:
            self.skipTest("symlinks are not available here")

        self.assertEqual(
            linux_camera.loopback_devices(sysfs),
            [("/dev/video10", "LumaLink Camera"), ("/dev/video2", "OBS Virtual Camera")],
        )

    def test_finds_nothing_without_the_module(self) -> None:
        sysfs = self.root / "video4linux"
        sysfs.mkdir()
        with mock.patch.object(linux_camera.sys, "platform", "linux"):
            self.assertIsNone(linux_camera.find_device(sysfs))


class LinuxBackendTests(unittest.TestCase):
    def test_explains_how_to_load_the_module_when_no_device_exists(self) -> None:
        with (
            mock.patch.object(camera_backend, "LINUX", True),
            mock.patch.object(linux_camera, "find_device", return_value=None),
        ):
            with self.assertRaisesRegex(RuntimeError, "v4l2loopback"):
                with VirtualCameraSession():
                    pass

    def test_writes_to_the_loopback_device(self) -> None:
        opened = []

        class FakeDevice:
            device = "LumaLink Camera"

            def __init__(self, path, width, height, fps) -> None:
                opened.append((path, width, height, fps))

            def send(self, frame) -> None:
                pass

            def close(self) -> None:
                pass

        with (
            mock.patch.object(camera_backend, "LINUX", True),
            mock.patch.object(linux_camera, "find_device", return_value="/dev/video10"),
            mock.patch.object(linux_camera, "LinuxCameraDevice", FakeDevice),
        ):
            with VirtualCameraSession() as session:
                self.assertEqual(session.device, "LumaLink Camera")

        self.assertEqual(opened, [("/dev/video10", 1280, 720, 30)])


if __name__ == "__main__":
    unittest.main()
