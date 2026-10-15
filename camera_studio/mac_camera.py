"""Sender for LumaLink Camera on macOS, the project's own camera extension.

The extension (``macvirtualcam/``) runs on its own, started by macOS. Frames
reach it through ``lumalink-camera feed``, a helper next to the app's
executable that reads raw RGB frames from its stdin and queues them into the
camera's input stream. When nothing is sent, the camera shows its idle image.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np

DEVICE_NAME = "LumaLink Camera"


def helper_path() -> Path:
    """Where ``lumalink-camera`` is: in the app bundle, or the local build."""
    if getattr(sys, "frozen", False):
        # LumaLink Studio.app/Contents/MacOS, next to the app's executable.
        return Path(sys.executable).resolve().parent / "lumalink-camera"
    return Path(__file__).resolve().parents[1] / "macvirtualcam" / "build" / "lumalink-camera"


def is_installed() -> bool:
    """Whether macOS offers LumaLink Camera to applications."""
    helper = helper_path()
    if sys.platform != "darwin" or not helper.is_file():
        return False
    try:
        result = subprocess.run([str(helper), "status"], capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


class MacCameraDevice:
    """Sends RGB frames to LumaLink Camera through ``lumalink-camera feed``."""

    device = DEVICE_NAME

    def __init__(self, helper: Path | None = None) -> None:
        self._process = subprocess.Popen(
            [str(helper or helper_path()), "feed"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    def send(self, frame: np.ndarray) -> None:
        stdin = self._process.stdin
        if stdin is None or self._process.poll() is not None:
            raise RuntimeError(self._failure())
        try:
            stdin.write(np.ascontiguousarray(frame, dtype=np.uint8).tobytes())
            stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise RuntimeError(self._failure()) from error

    def close(self) -> None:
        if self._process.stdin is not None:
            try:
                self._process.stdin.close()
            except OSError:
                pass
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()

    def _failure(self) -> str:
        try:
            self._process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        details = b""
        if self._process.stderr is not None and self._process.poll() is not None:
            details = self._process.stderr.read()
        return details.decode("utf-8", "replace").strip() or "LumaLink Camera перестала принимать кадры"
