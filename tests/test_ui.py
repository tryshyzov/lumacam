import os
import string
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import av  # noqa: E402
from PIL import Image  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from camera_studio import ui  # noqa: E402
from camera_studio.i18n import STRINGS, format_number, translate  # noqa: E402
from camera_studio.pause_frame import (  # noqa: E402
    paused_background,
    paused_frame,
    transition_frame,
)
from camera_studio.widgets import STYLE_SHEET  # noqa: E402


class FakeVirtualCamera:
    instances: list["FakeVirtualCamera"] = []

    def __init__(self, on_status) -> None:
        self.on_status = on_status
        self.images: list[Image.Image] = []
        self.stopped = False
        FakeVirtualCamera.instances.append(self)

    def start(self) -> None:
        self.on_status("Виртуальная камера запущена: Fake Camera", False)

    def submit(self, image: Image.Image) -> None:
        self.images.append(image)

    def stop(self) -> None:
        self.stopped = True


class FakeReceiver:
    def __init__(self) -> None:
        self.commands: list[dict[str, object]] = []
        self.stopped = False

    def send_control(self, command: dict[str, object]) -> None:
        self.commands.append(command)

    def stop(self) -> None:
        self.stopped = True


class TranslationTests(unittest.TestCase):
    def test_both_languages_define_the_same_strings(self) -> None:
        self.assertEqual(set(STRINGS["ru"]), set(STRINGS["en"]))

    def test_translations_use_the_same_placeholders(self) -> None:
        def fields(text: str) -> set[str]:
            return {name for _, name, _, _ in string.Formatter().parse(text) if name}

        for key, text in STRINGS["ru"].items():
            self.assertEqual(fields(text), fields(STRINGS["en"][key]), key)

    def test_formats_text_and_numbers_per_language(self) -> None:
        self.assertEqual(translate("en", "status_on", link="USB"), "Camera connected over USB")
        self.assertEqual(format_number("ru", 1.5, 1), "1,5")
        self.assertEqual(format_number("en", 1.5, 1), "1.5")


class PausedFrameTests(unittest.TestCase):
    def test_dims_the_picture_and_adds_a_badge_in_the_center(self) -> None:
        frame = paused_frame(Image.new("RGB", (1280, 720), (200, 100, 50)))

        self.assertEqual(frame.size, (1280, 720))
        self.assertEqual(frame.mode, "RGB")
        # Dimmed by a fifth away from the badge, white bars inside it.
        self.assertEqual(frame.getpixel((20, 20)), (160, 80, 40))
        self.assertEqual(frame.getpixel((632, 360)), (255, 255, 255))
        badge_red, _, _ = frame.getpixel((640, 322))
        self.assertLess(badge_red, 100)

    def test_transition_runs_from_the_plain_picture_to_the_paused_frame(self) -> None:
        source = Image.new("RGB", (1280, 720), (200, 100, 50))
        background = paused_background(source)

        start = transition_frame(source, background, 0.0)
        middle = transition_frame(source, background, 0.5)
        end = transition_frame(source, background, 1.0)

        self.assertEqual(start.getpixel((640, 360)), (200, 100, 50))
        self.assertEqual(middle.getpixel((20, 20)), (180, 90, 45))
        self.assertEqual(end.tobytes(), paused_frame(source).tobytes())
        # Half way the badge is smaller: its edge at full size is still picture.
        self.assertEqual(middle.getpixel((640, 320)), (180, 90, 45))

    def test_keeps_small_frames_usable(self) -> None:
        self.assertEqual(paused_frame(Image.new("RGB", (16, 10))).size, (16, 10))


class MainWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(STYLE_SHEET)

    def setUp(self) -> None:
        FakeVirtualCamera.instances.clear()
        patcher = mock.patch.object(ui, "VirtualCameraOutput", FakeVirtualCamera)
        patcher.start()
        self.addCleanup(patcher.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.settings_path = os.path.join(folder.name, "settings.ini")
        self.window = self.new_window()
        self.addCleanup(self.window.close)

    def new_window(self) -> ui.MainWindow:
        return ui.MainWindow(QSettings(self.settings_path, QSettings.Format.IniFormat))

    def connect_phone(self) -> FakeReceiver:
        receiver = FakeReceiver()
        self.window.receiver = receiver
        self.window.streaming = True
        self.show_frame((10, 20, 30))
        return receiver

    def show_frame(self, color: tuple[int, int, int]) -> Image.Image:
        image = Image.new("RGB", (1280, 720), color)
        self.window.latest_image = image
        self.window.frames.put_nowait(image)
        self.window._poll_stream()
        return image

    def test_starts_without_a_camera(self) -> None:
        window = self.window

        self.assertFalse(window.controls.isEnabled())
        self.assertFalse(window.pause_button.isEnabled())
        self.assertEqual(window.status_label.text(), "Камера не подключена")
        self.assertEqual(window.connect_button.text(), "Подключить камеру")
        self.assertFalse(window.preview.has_image())
        self.assertEqual(FakeVirtualCamera.instances, [])

    def test_window_buttons_follow_the_platform(self) -> None:
        for macos in (False, True):
            with self.subTest(macos=macos), mock.patch.object(ui, "MACOS", macos):
                window = self.new_window()
                self.addCleanup(window.close)
                flags = window.windowFlags()

                # macOS draws its own buttons over the title bar; Windows gets ours.
                self.assertEqual(window.maximize_button is None, macos)
                self.assertEqual(
                    bool(flags & ui.Qt.WindowType.FramelessWindowHint), not macos
                )
                self.assertEqual(
                    bool(flags & ui.Qt.WindowType.ExpandedClientAreaHint), macos
                )

    def test_usb_hint_points_mac_users_to_usb_debugging(self) -> None:
        for macos, words in ((False, "«USB-модем» и нажмите"), (True, "«Отладку по USB»")):
            with self.subTest(macos=macos), mock.patch.object(ui, "MACOS", macos):
                self.window.vpn_blocks_lan = False
                self.window._refresh()

                self.assertIn(words, self.window.hint_label.text())

    def test_first_frame_connects_and_starts_the_virtual_camera(self) -> None:
        receiver = self.connect_phone()
        self.window._poll_stream()
        window = self.window

        self.assertTrue(window.camera_connected)
        self.assertTrue(window.controls.isEnabled())
        self.assertEqual(window.status_label.text(), "Камера подключена по USB")
        self.assertEqual(window.connect_button.text(), "Отключить камеру")
        self.assertTrue(window.preview.has_image())
        self.assertEqual(len(FakeVirtualCamera.instances), 1)
        self.assertIn("Fake Camera", window.virtual_camera_note.text())
        self.assertEqual(receiver.commands[0]["resolution"], "720p")

    def test_disconnecting_stops_everything(self) -> None:
        receiver = self.connect_phone()
        camera = FakeVirtualCamera.instances[0]

        self.window._toggle_connection()

        self.assertTrue(receiver.stopped)
        self.assertTrue(camera.stopped)
        self.assertFalse(self.window.camera_connected)
        self.assertFalse(self.window.preview.has_image())
        self.assertEqual(self.window.connect_button.text(), "Подключить камеру")

    def test_stream_error_returns_to_the_disconnected_state(self) -> None:
        self.connect_phone()
        self.window._receive_status("Передача H.264 прервана: сбой", True)
        self.window._poll_stream()

        self.assertFalse(self.window.camera_connected)
        self.assertFalse(self.window.streaming)
        self.assertEqual(self.window.status_label.text(), "Передача H.264 прервана: сбой")
        self.assertTrue(FakeVirtualCamera.instances[0].stopped)

    def test_pause_freezes_the_picture_for_the_virtual_camera_too(self) -> None:
        self.connect_phone()
        camera = FakeVirtualCamera.instances[0]
        window = self.window

        window._toggle_pause()
        still = window.paused_image
        self.assertTrue(window.paused)
        self.assertIs(camera.images[-1], still)
        self.assertEqual(window.preview.note, "Пауза · стоп-кадр")
        self.assertTrue(window.preview.is_paused())

        # New frames from the phone do not replace the still frame.
        red = av.VideoFrame.from_image(Image.new("RGB", (1280, 720), "red"))
        window.decoder = mock.Mock(decode=lambda _packet: [red])
        window._receive_frame(b"packet")
        self.assertIs(camera.images[-1], still)
        self.assertTrue(window.frames.empty())

        window._toggle_pause()
        self.assertFalse(window.paused)
        self.assertFalse(window.preview.is_paused())
        window._receive_frame(b"packet")
        self.assertIsNot(camera.images[-1], still)

    def test_virtual_camera_steps_through_the_pause_animation(self) -> None:
        with mock.patch.object(ui, "animations_enabled", return_value=True):
            self.connect_phone()
            camera = FakeVirtualCamera.instances[0]
            window = self.window

            window._toggle_pause()
            first = camera.images[-1]
            self.assertEqual(first.getpixel((640, 360)), (10, 20, 30))

            source, background, final, started = window.pause_fade
            window.pause_fade = (source, background, final, started - 0.08)
            window._step_pause_fade()
            self.assertIsNot(camera.images[-1], final)
            self.assertIsNotNone(window.pause_fade)

            window.pause_fade = (source, background, final, started - 1.0)
            window._step_pause_fade()
            self.assertIs(camera.images[-1], final)
            self.assertIsNone(window.pause_fade)
            self.assertEqual(final.getpixel((632, 360)), (255, 255, 255))

            # Resuming fades the overlay out over the live picture.
            live = av.VideoFrame.from_image(Image.new("RGB", (1280, 720), (90, 90, 90)))
            window.decoder = mock.Mock(decode=lambda _packet: [live])
            with mock.patch.object(ui, "correct_stream_orientation", lambda image: image):
                window._toggle_pause()
                window._receive_frame(b"packet")
                self.assertIsInstance(camera.images[-1], Image.Image)
                faded_background, resumed = window.resume_fade
                window.resume_fade = (faded_background, resumed - 1.0)
                window._receive_frame(b"packet")
                self.assertIs(camera.images[-1], live)
                self.assertIsNone(window.resume_fade)

    def test_pause_is_instant_when_windows_animations_are_off(self) -> None:
        with mock.patch.object(ui, "animations_enabled", return_value=False):
            self.connect_phone()
            camera = FakeVirtualCamera.instances[0]

            self.window._toggle_pause()

            self.assertIsNone(self.window.pause_fade)
            self.assertEqual(camera.images[-1].getpixel((632, 360)), (255, 255, 255))
            self.window._toggle_pause()
            self.assertIsNone(self.window.resume_fade)

    def test_language_switch_keeps_the_words_the_phone_expects(self) -> None:
        window = self.window
        window._select_language("en")

        self.assertEqual(window.connect_button.text(), "Connect camera")
        self.assertEqual(window.status_label.text(), "Camera not connected")
        self.assertEqual(window.white_balance_select.itemText(1), "Incandescent")
        window.white_balance_select.setCurrentIndex(1)
        window.lens_seg.set_value("front")
        command = window.camera_command()
        self.assertEqual(command["iso"], "Авто")
        self.assertEqual(command["whiteBalance"], "Лампа")
        self.assertEqual(command["lens"], "Фронтальная")

    def test_controls_and_profile_make_the_camera_command(self) -> None:
        window = self.window
        window.autofocus_switch.setChecked(False)
        window.focus_slider.setValue(80)
        window.exposure_slider.setValue(-15)
        window.zoom_slider.setValue(25)
        window.iso_seg.set_value("400")
        window._select_profile(4)

        self.assertEqual(
            window.camera_command(),
            {
                "autofocus": False,
                "focus": 0.8,
                "exposure": -1.5,
                "iso": "400",
                "whiteBalance": "Авто",
                "lens": "Задняя",
                "zoom": 2.5,
                "fps": 60,
                "resolution": "1080p",
            },
        )
        self.assertEqual(window.exposure_value.text(), "−1,5 EV")

        window._reset_camera_controls()
        self.assertEqual(window.camera_command()["zoom"], 1.0)
        self.assertTrue(window.camera_command()["autofocus"])

    def test_settings_survive_a_restart(self) -> None:
        self.window._select_language("en")
        self.window.mirror_switch.setChecked(True)
        self.window.adb_switch.setChecked(False)
        self.window.settings.sync()

        reopened = self.new_window()
        self.addCleanup(reopened.close)

        self.assertEqual(reopened.language, "en")
        self.assertTrue(reopened.mirror_switch.isChecked())
        self.assertFalse(reopened.adb_switch.isChecked())
        self.assertTrue(reopened.preview.mirrored)


if __name__ == "__main__":
    unittest.main()
