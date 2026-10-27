import os
import string
import tempfile
from pathlib import Path
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import av  # noqa: E402
from PIL import Image  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from camera_studio import ui  # noqa: E402
from camera_studio.i18n import STRINGS, format_number, translate  # noqa: E402
from camera_studio.phones import Phone  # noqa: E402
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


class FakeFinder:
    def __init__(self, computer, on_change, use_adb=None) -> None:
        self.computer = computer
        self.on_change = on_change
        self.stopped = False

    def start(self) -> None:
        pass

    def stop(self) -> None:
        self.stopped = True


class FakePhoneReceiver(FakeReceiver):
    """Stands in for H264FrameReceiver; remembers whom it was asked to reach."""

    instances: list["FakePhoneReceiver"] = []

    def __init__(self, host, port, on_frame, on_status, usb, adb_fallback=True,
                 phone=None, hello=None, on_accepted=None, on_refused=None,
                 known_fingerprint=None, on_photo=None, on_phone_status=None) -> None:
        super().__init__()
        self.known_fingerprint = known_fingerprint
        self.on_photo = on_photo
        self.on_phone_status = on_phone_status
        self.phone = phone
        self.hello = hello
        self.on_status = on_status
        self.on_accepted = on_accepted
        self.on_refused = on_refused
        self.started = False
        FakePhoneReceiver.instances.append(self)

    def start(self) -> None:
        self.started = True


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
        FakePhoneReceiver.instances.clear()
        for name, fake in (
            ("VirtualCameraOutput", FakeVirtualCamera),
            ("PhoneFinder", FakeFinder),
            ("H264FrameReceiver", FakePhoneReceiver),
            ("local_network_blocked", lambda: False),
        ):
            patcher = mock.patch.object(ui, name, fake)
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
        self.window.link = "USB"
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
        self.assertEqual(window.status_label.text(), "Ищем телефон…")
        self.assertEqual(window.connect_button.text(), "Не подключаться")
        self.assertEqual(window.device_select.currentText(), "Телефоны не найдены")
        self.assertFalse(window.device_select.isEnabled())
        self.assertFalse(window.preview.has_image())
        self.assertEqual(FakeVirtualCamera.instances, [])
        self.assertEqual(FakePhoneReceiver.instances, [])

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
        for macos, words in ((False, "«USB-модемом»"), (True, "«Отладкой по USB»")):
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
        self.assertEqual(self.window.status_label.text(), "Камера не подключена")

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

        self.assertEqual(window.connect_button.text(), "Don't connect")
        self.assertEqual(window.status_label.text(), "Looking for the phone…")
        self.assertEqual(window.device_select.currentText(), "No phones found")
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
                "torch": False,
                "fps": 60,
                "resolution": "1080p",
            },
        )
        self.assertEqual(window.exposure_value.text(), "−1,5 EV")

        window._reset_camera_controls()
        self.assertEqual(window.camera_command()["zoom"], 1.0)
        self.assertTrue(window.camera_command()["autofocus"])

    def find(self, *phones: Phone) -> None:
        self.window.phone_lists.put(list(phones))
        self.window._poll_stream()

    def test_lists_phones_and_connects_to_one_on_usb_by_itself(self) -> None:
        wifi = Phone(id="p2", name="Pixel", link="wifi", address="10.0.0.3", version="0.6.0")
        usb = Phone(id="p1", name="Redmi", link="usb", address="192.168.42.129", version="0.6.0")
        self.find(wifi, usb)

        window = self.window
        labels = [window.device_select.itemText(i) for i in range(window.device_select.count())]
        self.assertEqual(labels, ["Pixel · Wi-Fi", "Redmi · USB"])
        (receiver,) = FakePhoneReceiver.instances
        self.assertIs(receiver.phone, usb)
        self.assertTrue(receiver.started)
        self.assertEqual(receiver.hello["id"], window.computer["id"])
        self.assertEqual(receiver.hello["token"], "")
        self.assertEqual(window.device_select.currentText(), "Redmi · USB")
        self.assertEqual(window.status_label.text(), "Подключаемся: Redmi · USB…")

    def test_an_unknown_wifi_phone_waits_to_be_picked(self) -> None:
        wifi = Phone(id="p2", name="Pixel", link="wifi", address="10.0.0.3", version="0.6.0")
        self.find(wifi)
        self.assertEqual(FakePhoneReceiver.instances, [])

        self.window.device_select.activated.emit(0)

        (receiver,) = FakePhoneReceiver.instances
        self.assertIs(receiver.phone, wifi)
        self.assertEqual(self.window.settings.value("phone_id"), "p2")

    def test_remembers_the_phone_its_token_and_its_zoom_range(self) -> None:
        usb = Phone(id="p1", name="Redmi", link="usb", address="192.168.42.129", version="0.6.0")
        self.find(usb)
        receiver = FakePhoneReceiver.instances[0]
        receiver.on_status("Разрешите подключение на экране телефона", False)
        self.window._poll_stream()
        self.assertEqual(self.window.status_label.text(), "Разрешите подключение на экране телефона")

        receiver.on_accepted({
            "status": "ok", "token": "t-1",
            "camera": {"id": "p1", "lenses": {"Задняя": {"zoom": [0.5, 8]}, "Фронтальная": {"zoom": [1, 4]}}},
        })
        self.window._poll_stream()

        window = self.window
        self.assertEqual(window.settings.value("tokens/p1"), "t-1")
        self.assertEqual(window.settings.value("phone_id"), "p1")
        self.assertEqual((window.zoom_slider.minimum(), window.zoom_slider.maximum()), (5, 80))
        low, high = window.scale_labels["zoom"]
        self.assertEqual((low.text(), high.text()), ("0,5×", "8×"))
        window.lens_seg.set_value("front")
        window._lens_changed()
        self.assertEqual((window.zoom_slider.minimum(), window.zoom_slider.maximum()), (10, 40))

    def test_offers_60_fps_only_where_the_phone_gives_it(self) -> None:
        usb = Phone(id="p1", name="Redmi", link="usb", address="192.168.42.129", version="0.6.0")
        window = self.window
        window._select_profile(4)  # 1080p, 60 fps
        self.find(usb)
        FakePhoneReceiver.instances[0].on_accepted({
            "status": "ok",
            "camera": {"id": "p1", "lenses": {
                "Задняя": {"zoom": [1, 10], "fps": {"720p": [30, 60], "1080p": [30]}},
                "Фронтальная": {"zoom": [1, 4], "fps": {"720p": [30], "1080p": [30, 60]}},
            }},
        })
        window._poll_stream()

        self.assertEqual(ui.PROFILES[window.profile_index][:2], ("1080p", 30))
        enabled = [action.isEnabled() for action in window.profile_actions]
        # 480p is not listed: unknown, so offered; 720p has 60, 1080p not.
        self.assertEqual(enabled, [True, True, True, True, False])
        window.lens_seg.set_value("front")
        window._lens_changed()
        window._refresh()
        enabled = [action.isEnabled() for action in window.profile_actions]
        self.assertEqual(enabled, [True, True, False, True, True])

    def test_keeps_an_encrypted_phones_key_and_shows_the_pairing_code(self) -> None:
        wifi = Phone(id="p2", name="Pixel", link="wifi", address="10.0.0.3", version="0.7.0", tls=True)
        self.find(wifi)
        self.window.device_select.activated.emit(0)
        receiver = FakePhoneReceiver.instances[0]
        self.assertIsNone(receiver.known_fingerprint)
        receiver.on_status("Разрешите подключение на экране телефона. Код: 300 879", False)
        self.window._poll_stream()
        self.assertIn("300 879", self.window.status_label.text())

        receiver.on_accepted({"status": "ok", "token": "t-2", "fingerprint": "ab" * 32, "camera": {"id": "p2"}})
        self.window._poll_stream()
        self.assertEqual(self.window.settings.value("pins/p2"), "ab" * 32)

        # The next connection checks the phone against that key.
        self.window._disconnect()
        self.window.device_select.activated.emit(0)
        self.assertEqual(FakePhoneReceiver.instances[-1].known_fingerprint, "ab" * 32)

    def test_reconnects_after_a_drop_and_ignores_the_old_connection(self) -> None:
        usb = Phone(id="p1", name="Redmi", link="usb", address="192.168.42.129", version="0.6.0")
        self.find(usb)
        first = FakePhoneReceiver.instances[0]
        first.on_status("Передача H.264 прервана: сбой", True)
        self.window._poll_stream()
        self.assertTrue(first.stopped)
        self.assertEqual(len(FakePhoneReceiver.instances), 1)  # waits RETRY_DELAY first

        self.window.retry_at = 0.0
        self.window._poll_stream()
        self.assertEqual(len(FakePhoneReceiver.instances), 2)
        first.on_status("Передача H.264 прервана: ещё раз", True)
        self.window._poll_stream()
        self.assertFalse(FakePhoneReceiver.instances[1].stopped)

    def test_a_refusing_phone_is_left_alone_for_a_while(self) -> None:
        usb = Phone(id="p1", name="Redmi", link="usb", address="192.168.42.129", version="0.6.0")
        self.find(usb)
        receiver = FakePhoneReceiver.instances[0]
        receiver.on_refused(ui.PhoneRefused("Телефон уже передаёт видео на другой компьютер", "busy"))
        receiver.on_status("Телефон уже передаёт видео на другой компьютер", True)
        self.window._poll_stream()
        self.window.retry_at = 0.0
        self.window._poll_stream()

        self.assertEqual(len(FakePhoneReceiver.instances), 1)
        self.assertEqual(self.window.status_label.text(), "Телефон уже передаёт видео на другой компьютер")

    def test_disconnecting_stops_connecting_by_itself(self) -> None:
        usb = Phone(id="p1", name="Redmi", link="usb", address="192.168.42.129", version="0.6.0")
        self.find(usb)
        self.window._toggle_connection()
        self.window._poll_stream()

        self.assertTrue(FakePhoneReceiver.instances[0].stopped)
        self.assertEqual(len(FakePhoneReceiver.instances), 1)
        self.window._toggle_connection()
        self.assertEqual(len(FakePhoneReceiver.instances), 2)

    def test_the_look_styles_frames_and_survives_a_restart(self) -> None:
        window = self.window
        window.look_select.setCurrentIndex(window.look_select.findData("mono"))
        window.look_sliders["contrast"][0].setValue(30)
        decoded = av.VideoFrame.from_image(Image.new("RGB", (64, 36), (200, 40, 40))).reformat(format="yuv420p")
        for _ in range(2):
            styled = window._styled(decoded)
        r, g, b = styled.to_image().getpixel((32, 18))
        self.assertLessEqual(max(r, g, b) - min(r, g, b), 3)
        self.assertEqual(window.look_sliders["contrast"][1].text(), "+30")
        window.settings.sync()

        reopened = self.new_window()
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.look_select.currentData(), "mono")
        self.assertEqual(reopened.look_sliders["contrast"][0].value(), 30)

        reopened._reset_look()
        self.assertTrue(reopened.current_look().neutral)

    def test_takes_a_photo_and_saves_it_in_pictures(self) -> None:
        usb = Phone(id="p1", name="Redmi", link="usb", address="192.168.42.129", version="0.7.0", photo=True)
        self.find(usb)
        receiver = FakePhoneReceiver.instances[0]
        receiver.on_accepted({"status": "ok", "camera": {"id": "p1", "photo": True}})
        self.window.receiver = receiver
        self.show_frame((10, 20, 30))
        self.assertTrue(self.window.photo_button.isEnabled())

        self.window._take_photo()
        self.window._take_photo()
        self.assertEqual([c.get("photo") for c in receiver.commands][-2:], [1, 2])

        with tempfile.TemporaryDirectory() as folder, mock.patch.object(
            ui.MainWindow, "photo_folder", staticmethod(lambda: Path(folder))
        ):
            receiver.on_photo(b"\xff\xd8jpeg\xff\xd9")
            receiver.on_photo(b"\xff\xd8jpeg\xff\xd9")
            self.window._poll_stream()
            saved = sorted(Path(folder).glob("*.jpg"))
            self.assertEqual(len(saved), 2)
            self.assertEqual(saved[0].read_bytes(), b"\xff\xd8jpeg\xff\xd9")
        self.assertIn("Снимок сохранён", self.window.preview.note)

        receiver.on_photo(b"")
        self.window._poll_stream()
        self.assertEqual(self.window.preview.note, "Телефон не смог сделать снимок")

    def connect_v2_phone(self, lenses: dict | None = None) -> FakePhoneReceiver:
        usb = Phone(id="p1", name="Redmi", link="usb", address="192.168.42.129", version="0.8.0")
        self.find(usb)
        receiver = FakePhoneReceiver.instances[-1]
        receiver.on_accepted({"status": "ok", "camera": {"id": "p1", "lenses": lenses or {}}})
        self.window.receiver = receiver
        self.show_frame((10, 20, 30))
        return receiver

    def test_says_it_understands_phone_status_and_shows_it(self) -> None:
        receiver = self.connect_v2_phone()
        self.assertEqual(receiver.hello["features"], ["status"])
        receiver.on_phone_status({"battery": 64, "charging": True, "temperature": 36.4})
        self.window._poll_stream()
        self.assertEqual(self.window.phone_state_label.text(), "Телефон: 64% · заряжается · 36°")
        receiver.on_phone_status({"battery": 50, "charging": False, "temperature": 44})
        self.window._poll_stream()
        self.assertIn("перегревается", self.window.phone_state_label.text())
        receiver.on_phone_status({"battery": 9, "charging": False, "temperature": 30})
        self.window._poll_stream()
        self.assertIn("Батарея садится", self.window.phone_state_label.text())

    def test_torch_only_where_the_lens_has_one(self) -> None:
        receiver = self.connect_v2_phone({"Задняя": {"torch": True}, "Фронтальная": {}})
        window = self.window
        self.assertTrue(window.torch_switch.isEnabled())
        window.torch_switch.setChecked(True)
        self.assertTrue(window.camera_command()["torch"])
        window.lens_seg.set_value("front")
        window._lens_changed()
        self.assertFalse(window.torch_switch.isEnabled())
        self.assertFalse(window.camera_command()["torch"])

    def test_a_click_on_the_picture_meters_there(self) -> None:
        receiver = self.connect_v2_phone()
        self.window._meter_at(0.25, 0.75)
        meter = receiver.commands[-1]["meter"]
        self.assertEqual((meter["x"], meter["y"], meter["id"]), (0.25, 0.75, 1))
        # While framing shows only part of the picture, the click maps into it.
        with mock.patch.object(self.window.effects, "shown_part", return_value=(0.2, 0.1, 0.5, 0.5)):
            self.window._meter_at(0.5, 0.5)
        meter = receiver.commands[-1]["meter"]
        self.assertAlmostEqual(meter["x"], 0.45)
        self.assertAlmostEqual(meter["y"], 0.35)
        self.assertEqual(meter["id"], 2)

    def test_saves_and_applies_a_preset(self) -> None:
        window = self.window
        window.exposure_slider.setValue(-10)
        window.look_select.setCurrentIndex(window.look_select.findData("warm"))
        window.framing_switch.setChecked(True)
        with mock.patch.object(ui.QInputDialog, "getText", return_value=("Созвон", True)):
            window._save_preset()
        self.assertEqual(window.preset_select.currentText(), "Созвон")

        window.exposure_slider.setValue(5)
        window.look_select.setCurrentIndex(0)
        window.framing_switch.setChecked(False)
        window._apply_preset(window.preset_select.findData("Созвон"))
        self.assertEqual(window.exposure_slider.value(), -10)
        self.assertEqual(window.look_select.currentData(), "warm")
        self.assertTrue(window.framing_switch.isChecked())

        window.preset_select.setCurrentIndex(window.preset_select.findData("Созвон"))
        window._delete_preset()
        self.assertEqual(window.preset_select.findData("Созвон"), -1)

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
