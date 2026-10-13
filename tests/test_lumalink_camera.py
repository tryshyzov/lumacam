import ctypes
import os
import struct
import sys
import unittest
from unittest import mock

import numpy as np

from camera_studio import lumalink_camera
from camera_studio.camera_backend import VirtualCameraSession
from camera_studio.lumalink_camera import LumaLinkCameraDevice, pack_frame
from camera_studio.virtual_camera import (
    IDLE_BACKGROUND,
    IDLE_GLYPH,
    OUTPUT_HEIGHT,
    OUTPUT_WIDTH,
    idle_frame,
)

HEADER_SIZE = 32
SHARED_SIZE = HEADER_SIZE + 64


class FakeFilter:
    """The filter's side of the shared objects, as created in shared.inl."""

    def __init__(self, prefix: str) -> None:
        self._kernel32 = lumalink_camera._load_kernel32()
        self._kernel32.CreateMutexA.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p]
        self._kernel32.CreateMutexA.restype = ctypes.c_void_p
        self._kernel32.CreateFileMappingA.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_char_p,
        ]
        self._kernel32.CreateFileMappingA.restype = ctypes.c_void_p
        self._names = {
            kind: f"{prefix}_{kind}".encode("ascii")
            for kind in ("Mutx", "Want", "Sent", "Data")
        }
        self._handles: list[int] = []
        self.view = 0
        self.want_frame = 0
        self.sent_frame = 0
        self._handles.append(self._kernel32.CreateMutexA(None, False, self._names["Mutx"]))

    def finish_opening(self) -> bool:
        """Second filter pass: possible only once the sender created its event."""
        kernel32 = self._kernel32
        self.want_frame = kernel32.OpenEventA(0x0002, False, self._names["Want"])
        if not self.want_frame:
            return False
        self.sent_frame = kernel32.CreateEventA(None, False, False, self._names["Sent"])
        mapping = kernel32.CreateFileMappingA(
            ctypes.c_void_p(-1), None, 0x04, 0, SHARED_SIZE, self._names["Data"]
        )
        self.view = kernel32.MapViewOfFile(mapping, 0x0002, 0, 0, 0)
        ctypes.memmove(self.view, struct.pack("<I", SHARED_SIZE - HEADER_SIZE), 4)
        self._handles += [self.want_frame, self.sent_frame, mapping]
        return True

    def request_frame(self) -> None:
        self._kernel32.SetEvent(self.want_frame)

    def frame_was_sent(self) -> bool:
        return self._kernel32.WaitForSingleObject(self.sent_frame, 0) == 0

    def header(self) -> tuple[int, ...]:
        return struct.unpack("<I7i", ctypes.string_at(self.view, HEADER_SIZE))

    def pixels(self, count: int) -> bytes:
        return ctypes.string_at(self.view + HEADER_SIZE, count)

    def close(self) -> None:
        if self.view:
            self._kernel32.UnmapViewOfFile(self.view)
        for handle in self._handles:
            self._kernel32.CloseHandle(handle)


class FramePackingTests(unittest.TestCase):
    def test_packs_rgb_rows_bottom_up_with_opaque_alpha(self) -> None:
        frame = np.array(
            [[[1, 2, 3], [4, 5, 6]], [[7, 8, 9], [10, 11, 12]]],
            dtype=np.uint8,
        )

        packed = pack_frame(frame)

        self.assertEqual(packed.shape, (2, 2, 4))
        self.assertEqual(
            packed.tobytes(),
            bytes([7, 8, 9, 255, 10, 11, 12, 255, 1, 2, 3, 255, 4, 5, 6, 255]),
        )

    def test_reuses_the_buffer_for_frames_of_the_same_size(self) -> None:
        first = pack_frame(np.zeros((2, 2, 3), dtype=np.uint8))
        second = pack_frame(np.full((2, 2, 3), 9, dtype=np.uint8), first)

        self.assertIs(second, first)
        self.assertEqual(second[0, 0].tolist(), [9, 9, 9, 255])


@unittest.skipUnless(sys.platform == "win32", "LumaLink Camera is Windows-only")
class SenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prefix = f"LumaLinkTest{os.getpid()}_{id(self)}"
        self.device = LumaLinkCameraDevice(name_prefix=self.prefix, timeout_ms=750)
        self.addCleanup(self.device.close)
        self.frame = np.array([[[10, 20, 30], [40, 50, 60]]], dtype=np.uint8)

    def test_drops_frames_while_no_application_uses_the_camera(self) -> None:
        self.assertFalse(self.device.send(self.frame))

    def test_delivers_frames_once_the_filter_is_streaming(self) -> None:
        camera = FakeFilter(self.prefix)
        self.addCleanup(camera.close)

        self.assertFalse(camera.finish_opening())
        self.assertFalse(self.device.send(self.frame))
        self.assertTrue(camera.finish_opening())

        self.assertTrue(self.device.send(self.frame))
        self.assertTrue(camera.frame_was_sent())
        self.assertEqual(camera.header()[1:], (2, 1, 2, 0, 1, 0, 750))
        self.assertEqual(camera.pixels(8), bytes([10, 20, 30, 255, 40, 50, 60, 255]))

    def test_waits_for_the_filter_to_request_the_next_frame(self) -> None:
        camera = FakeFilter(self.prefix)
        self.addCleanup(camera.close)
        camera.finish_opening()
        self.device.send(self.frame)
        camera.finish_opening()
        self.assertTrue(self.device.send(self.frame))
        camera.frame_was_sent()

        self.assertFalse(self.device.send(self.frame))
        self.assertFalse(camera.frame_was_sent())
        camera.request_frame()
        self.assertTrue(self.device.send(self.frame))
        self.assertTrue(camera.frame_was_sent())

    def test_refuses_frames_larger_than_the_shared_block(self) -> None:
        camera = FakeFilter(self.prefix)
        self.addCleanup(camera.close)
        camera.finish_opening()
        self.device.send(self.frame)
        camera.finish_opening()

        self.assertFalse(self.device.send(np.zeros((8, 8, 3), dtype=np.uint8)))
        self.assertEqual(camera.header()[1], 0)


class BackendSelectionTests(unittest.TestCase):
    def test_prefers_the_installed_lumalink_camera(self) -> None:
        class FakeDevice:
            device = "LumaLink Camera"

            def __init__(self) -> None:
                self.frames: list[np.ndarray] = []
                self.closed = False

            def send(self, frame: np.ndarray) -> bool:
                self.frames.append(frame)
                return True

            def close(self) -> None:
                self.closed = True

        with (
            mock.patch.object(lumalink_camera, "is_registered", return_value=True),
            mock.patch.object(lumalink_camera, "LumaLinkCameraDevice", FakeDevice),
        ):
            with VirtualCameraSession() as session:
                self.assertEqual(session.device, "LumaLink Camera")
                frame = np.zeros((2, 2, 3), dtype=np.uint8)
                session.send(frame)
                device = session._camera

        self.assertIs(device.frames[0], frame)
        self.assertTrue(device.closed)


class IdleFrameTests(unittest.TestCase):
    def test_draws_a_camera_glyph_on_gray(self) -> None:
        frame = idle_frame()

        self.assertEqual(frame.shape, (OUTPUT_HEIGHT, OUTPUT_WIDTH, 3))
        self.assertTrue(frame.flags.c_contiguous)
        self.assertEqual(frame[5, 5].tolist(), [IDLE_BACKGROUND] * 3)
        # Body of the glyph, then its lens wedge, then the gap between them.
        self.assertEqual(frame[360, 700].tolist(), [IDLE_GLYPH] * 3)
        self.assertEqual(frame[360, 560].tolist(), [IDLE_GLYPH] * 3)
        self.assertEqual(frame[360, 597].tolist(), [IDLE_BACKGROUND] * 3)


if __name__ == "__main__":
    unittest.main()
