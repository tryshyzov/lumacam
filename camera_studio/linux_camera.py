"""LumaLink Camera on Linux: a v4l2loopback device the system offers to apps.

The v4l2loopback kernel module creates video devices that any application
reads like a webcam. The .deb package installs it and loads one device named
"LumaLink Camera" at boot (``linux/modprobe-lumalink.conf``); with an AppImage
the user loads it once by hand (``MODULE_HINT``). Frames are written through
pyvirtualcam's v4l2loopback backend.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

DEVICE_NAME = "LumaLink Camera"
_SYSFS = Path("/sys/class/video4linux")

MODULE_HINT = (
    "Виртуальная камера на Linux работает через модуль ядра v4l2loopback. "
    "Установите его и создайте камеру командами:\n"
    "sudo apt install v4l2loopback-dkms\n"
    'sudo modprobe v4l2loopback card_label="LumaLink Camera" exclusive_caps=1\n'
    "(пакет .deb LumaLink Studio делает это сам)."
)


def loopback_devices(sysfs: Path = _SYSFS) -> list[tuple[str, str]]:
    """Every v4l2loopback device as (path, name), LumaLink Camera first."""
    devices = []
    for entry in sorted(sysfs.glob("video*"), key=lambda item: int(item.name[5:] or 0)):
        try:
            name = (entry / "name").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        # A loopback device has no physical parent; real cameras link to USB.
        device_link = entry / "device"
        if device_link.exists() and "virtual" not in str(device_link.resolve()):
            continue
        devices.append((f"/dev/{entry.name}", name))
    devices.sort(key=lambda item: item[1] != DEVICE_NAME)
    return devices


def find_device(sysfs: Path = _SYSFS) -> str | None:
    """The device to write to: LumaLink Camera, else any loopback device."""
    if not sys.platform.startswith("linux"):
        return None
    devices = loopback_devices(sysfs)
    return devices[0][0] if devices else None


class LinuxCameraDevice:
    """Writes RGB frames to the loopback device through pyvirtualcam."""

    def __init__(self, path: str, width: int, height: int, fps: int) -> None:
        import pyvirtualcam

        self._camera = pyvirtualcam.Camera(
            width=width,
            height=height,
            fps=fps,
            fmt=pyvirtualcam.PixelFormat.RGB,
            backend="v4l2loopback",
            device=path,
            print_fps=False,
        )
        name = next((label for device, label in loopback_devices() if device == path), "")
        self.device = name or path

    def send(self, frame: np.ndarray) -> None:
        self._camera.send(frame)

    def close(self) -> None:
        self._camera.close()
