from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

import numpy as np

OUTPUT_WIDTH = 1280
OUTPUT_HEIGHT = 720
OUTPUT_FPS = 30


_BACKENDS = (
    ("obs", "OBS Virtual Camera"),
    ("unitycapture", "Unity Video Capture"),
)


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
        for backend, device in _BACKENDS:
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
        raise RuntimeError(
            "Не найден доступный backend виртуальной камеры. "
            "Установите OBS Studio с компонентом OBS Virtual Camera "
            "или отдельно Unity Capture.\n"
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
