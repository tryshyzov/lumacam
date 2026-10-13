"""Sender for LumaLink Camera, the project's own DirectShow virtual camera.

The filter (``virtualcam/``) runs inside whichever application opens the
camera. While it is streaming it owns a named mutex and a shared-memory block;
this module writes video frames into that block. When nothing is sent, the
filter shows its idle image by itself.
"""

from __future__ import annotations

import ctypes
import struct

import numpy as np

DEVICE_NAME = "LumaLink Camera"
FILTER_CLSID = "{54351CBF-A203-4238-A64C-8606B3446010}"
OBJECT_NAME_PREFIX = "LumaLinkCam"

# SharedMemHeader in virtualcam/shared.inl: maxSize, then width, height,
# stride, format, resizemode, mirrormode and timeout, followed by the pixels.
_HEADER = struct.Struct("<I7i")
_FRAME_FIELDS = struct.Struct("<7i")
_FORMAT_UINT8 = 0
_RESIZE_LINEAR = 1
_MIRROR_DISABLED = 0

_SYNCHRONIZE = 0x00100000
_EVENT_MODIFY_STATE = 0x0002
_FILE_MAP_WRITE = 0x0002
_WAIT_OBJECT_0 = 0x00
_WAIT_ABANDONED = 0x80
_LOCK_TIMEOUT_MS = 1000


def is_registered() -> bool:
    """Whether the LumaLink Camera filter is installed on this computer."""
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            rf"SOFTWARE\Classes\CLSID\{FILTER_CLSID}",
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        )
    except (ImportError, OSError):
        return False
    winreg.CloseKey(key)
    return True


def pack_frame(frame: np.ndarray, out: np.ndarray | None = None) -> np.ndarray:
    """Convert a top-down RGB frame to the bottom-up RGBA rows the filter reads."""
    height, width = frame.shape[:2]
    if out is None or out.shape != (height, width, 4):
        out = np.empty((height, width, 4), dtype=np.uint8)
        out[..., 3] = 255
    out[..., :3] = frame[::-1]
    return out


class LumaLinkCameraDevice:
    """Writes frames to the LumaLink Camera filter while an application uses it."""

    device = DEVICE_NAME

    def __init__(
        self,
        name_prefix: str = OBJECT_NAME_PREFIX,
        timeout_ms: int = 1000,
    ) -> None:
        self._names = {
            kind: f"{name_prefix}_{kind}".encode("ascii")
            for kind in ("Mutx", "Want", "Sent", "Data")
        }
        self._timeout_ms = timeout_ms
        self._kernel32 = _load_kernel32()
        self._mutex: int | None = None
        self._want_frame: int | None = None
        self._sent_frame: int | None = None
        self._mapping: int | None = None
        self._view: int | None = None
        self._packed: np.ndarray | None = None

    def send(self, frame: np.ndarray) -> bool:
        """Deliver ``frame`` if an application is waiting for one.

        Returns ``False`` when no application has the camera open or it has
        not asked for the next frame yet; the frame is then simply dropped.
        """
        kernel32 = self._kernel32
        if not self._open():
            return False
        # The filter signals this event each time it wants a frame, so nothing
        # is copied while no application is reading. It only starts asking
        # once the block holds a frame, so the first one is sent unprompted.
        wanted = kernel32.WaitForSingleObject(self._want_frame, 0) == _WAIT_OBJECT_0
        (_, shared_width) = struct.unpack_from("<Ii", ctypes.string_at(self._view, 8))
        if not wanted and shared_width:
            return False

        self._packed = pack_frame(frame, self._packed)
        packed = self._packed
        height, width = packed.shape[:2]
        if not self._lock():
            return False
        try:
            (max_size,) = struct.unpack_from("<I", ctypes.string_at(self._view, 4))
            if max_size < packed.nbytes:
                return False
            fields = _FRAME_FIELDS.pack(
                width,
                height,
                width,
                _FORMAT_UINT8,
                _RESIZE_LINEAR,
                _MIRROR_DISABLED,
                self._timeout_ms,
            )
            ctypes.memmove(self._view + 4, fields, len(fields))
            ctypes.memmove(self._view + _HEADER.size, packed.ctypes.data, packed.nbytes)
        finally:
            kernel32.ReleaseMutex(self._mutex)
        kernel32.SetEvent(self._sent_frame)
        return True

    def close(self) -> None:
        kernel32 = self._kernel32
        if self._view is not None:
            kernel32.UnmapViewOfFile(self._view)
            self._view = None
        for name in ("_mapping", "_sent_frame", "_want_frame", "_mutex"):
            handle = getattr(self, name)
            if handle is not None:
                kernel32.CloseHandle(handle)
                setattr(self, name, None)

    def _lock(self) -> bool:
        result = self._kernel32.WaitForSingleObject(self._mutex, _LOCK_TIMEOUT_MS)
        return result in (_WAIT_OBJECT_0, _WAIT_ABANDONED)

    def _open(self) -> bool:
        """Attach to the filter's shared objects.

        The filter and this sender each create part of the set and open the
        rest, so attaching can take a few calls. Handles obtained so far are
        kept between calls: the filter cannot finish its side until the
        frame-request event created here exists.
        """
        if self._view is not None:
            return True
        kernel32 = self._kernel32
        names = self._names
        if self._mutex is None:
            self._mutex = kernel32.OpenMutexA(_SYNCHRONIZE, False, names["Mutx"]) or None
            if self._mutex is None:
                return False
        if not self._lock():
            return False
        try:
            if self._want_frame is None:
                self._want_frame = (
                    kernel32.CreateEventA(None, False, False, names["Want"]) or None
                )
            if self._want_frame is None:
                return False
            if self._sent_frame is None:
                self._sent_frame = (
                    kernel32.OpenEventA(_EVENT_MODIFY_STATE, False, names["Sent"]) or None
                )
            if self._sent_frame is None:
                return False
            if self._mapping is None:
                self._mapping = (
                    kernel32.OpenFileMappingA(_FILE_MAP_WRITE, False, names["Data"]) or None
                )
            if self._mapping is None:
                return False
            self._view = (
                kernel32.MapViewOfFile(self._mapping, _FILE_MAP_WRITE, 0, 0, 0) or None
            )
            return self._view is not None
        finally:
            kernel32.ReleaseMutex(self._mutex)


def _load_kernel32() -> ctypes.CDLL:
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except (AttributeError, OSError) as error:
        raise RuntimeError("LumaLink Camera доступна только в Windows") from error
    handle, dword, boolean = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int
    signatures = {
        "OpenMutexA": ([dword, boolean, ctypes.c_char_p], handle),
        "CreateEventA": ([ctypes.c_void_p, boolean, boolean, ctypes.c_char_p], handle),
        "OpenEventA": ([dword, boolean, ctypes.c_char_p], handle),
        "OpenFileMappingA": ([dword, boolean, ctypes.c_char_p], handle),
        "MapViewOfFile": ([handle, dword, dword, dword, ctypes.c_size_t], ctypes.c_void_p),
        "UnmapViewOfFile": ([ctypes.c_void_p], boolean),
        "WaitForSingleObject": ([handle, dword], dword),
        "ReleaseMutex": ([handle], boolean),
        "SetEvent": ([handle], boolean),
        "CloseHandle": ([handle], boolean),
    }
    for name, (argument_types, result_type) in signatures.items():
        function = getattr(kernel32, name)
        function.argtypes = argument_types
        function.restype = result_type
    return kernel32
