import unittest

from camera_studio.models import (
    CameraControls,
    ConnectionMode,
    DeviceSession,
    SessionStatus,
)


class CameraModelsTests(unittest.TestCase):
    def test_new_session_is_disconnected_and_defaults_to_wifi(self) -> None:
        session = DeviceSession()

        self.assertEqual(session.status, SessionStatus.DISCONNECTED)
        self.assertEqual(session.mode, ConnectionMode.WIFI)
        self.assertIsNone(session.device_name)
        self.assertIsNone(session.error)

    def test_camera_controls_have_safe_baseline_defaults(self) -> None:
        controls = CameraControls()

        self.assertTrue(controls.autofocus)
        self.assertEqual(controls.iso, 100)
        self.assertEqual(controls.white_balance_kelvin, 5000)
        self.assertEqual(controls.lens, "main")
        self.assertEqual(controls.zoom, 1.0)
        self.assertEqual(controls.fps, 30)

    def test_session_can_represent_usb_connection(self) -> None:
        session = DeviceSession(mode=ConnectionMode.USB)

        self.assertEqual(session.mode, ConnectionMode.USB)
        self.assertEqual(session.status, SessionStatus.DISCONNECTED)


if __name__ == "__main__":
    unittest.main()
