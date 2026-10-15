from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Protocol

import numpy as np

from camera_studio import lumalink_camera

OUTPUT_WIDTH = 1280
OUTPUT_HEIGHT = 720
OUTPUT_FPS = 30


MACOS = sys.platform == "darwin"

_BACKENDS = (
    ("obs", "OBS Virtual Camera"),
    ("unitycapture", "Unity Video Capture"),
)
# Unity Capture is Windows-only; on macOS pyvirtualcam has just OBS.
_MACOS_BACKENDS = (("obs", "OBS Virtual Camera"),)


class CameraDevice(Protocol):
    device: str

    def send(self, frame: np.ndarray) -> None: ...

    def close(self) -> None: ...


class VirtualCameraSession:
    def __init__(
        self,
        camera_factory: Callable[..., CameraDevice] | None = None,
    ) -> None:
        self._camera_factory = camera_factory
        self._pixel_format: object = "RGB"
        self._camera: CameraDevice | None = None
        self.device = ""

    def __enter__(self) -> VirtualCameraSession:
        camera_factory = self._camera_factory
        if camera_factory is None and lumalink_camera.is_registered():
            # The project's own camera needs no third-party backend.
            camera = lumalink_camera.LumaLinkCameraDevice()
            self._camera = camera
            self.device = camera.device
            return self
        if camera_factory is None:
            try:
                import pyvirtualcam
            except ImportError as error:
                raise RuntimeError(
                    "Не найден модуль вывода виртуальной камеры. "
                    "Переустановите LumaLink Studio."
                ) from error
            camera_factory = pyvirtualcam.Camera
            self._pixel_format = pyvirtualcam.PixelFormat.RGB

        errors: list[str] = []
        for backend, device in _MACOS_BACKENDS if MACOS else _BACKENDS:
            try:
                camera = camera_factory(
                    width=OUTPUT_WIDTH,
                    height=OUTPUT_HEIGHT,
                    fps=OUTPUT_FPS,
                    fmt=self._pixel_format,
                    backend=backend,
                    device=device,
                    print_fps=False,
                )
            except (ImportError, OSError, RuntimeError) as error:
                errors.append(f"{device}: {error}")
                continue

            self._camera = camera
            self.device = camera.device
            return self

        details = "\n".join(errors)
        if MACOS:
            raise RuntimeError(
                "На macOS виртуальная камера пока работает через OBS. Установите "
                "OBS Studio 30 или новее, один раз нажмите в нём «Запустить "
                "виртуальную камеру» и разрешите системное расширение OBS в "
                "Системных настройках.\n"
                f"{details}"
            )
        raise RuntimeError(
            "Камера LumaLink Camera не установлена. Установите LumaLink Studio "
            "через установщик либо поставьте OBS Studio или Unity Capture.\n"
            f"{details}"
        )

    def __exit__(self, *_args: object) -> None:
        camera = self._camera
        self._camera = None
        if camera is not None:
            camera.close()

    def send(self, frame: np.ndarray) -> None:
        if self._camera is None:
            raise RuntimeError("Виртуальная камера не запущена")
        self._camera.send(frame)
