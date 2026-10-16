import json
import socket
import threading
import time
import unittest

from camera_studio import phones
from camera_studio.phones import Phone, PhoneFinder, adb_phones, parse_reply, sort_phones, zoom_range


def camera_reply(**info) -> bytes:
    return b"LUMALINK-CAMERA-2 " + json.dumps(info).encode("utf-8")


class FakePhone:
    """Answers discovery like a phone on loopback; version 1 or 2."""

    def __init__(self, info: dict | None) -> None:
        self.info = info
        self.probes: list[bytes] = []
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.settimeout(0.1)
        self.port = self.socket.getsockname()[1]
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        while self.running:
            try:
                request, sender = self.socket.recvfrom(4096)
            except OSError:
                continue
            self.probes.append(request)
            if request == b"LUMALINK-DISCOVER-1":
                self.socket.sendto(b"LUMALINK-CAMERA-1", sender)
            elif request.startswith(b"LUMALINK-DISCOVER-2 ") and self.info is not None:
                self.socket.sendto(camera_reply(**self.info), sender)

    def close(self) -> None:
        self.running = False
        self.thread.join(timeout=1)
        self.socket.close()


class ParseTests(unittest.TestCase):
    def test_reads_a_version_2_answer(self) -> None:
        phone = parse_reply(
            camera_reply(id="p1", name="Redmi Note 9 Pro", link="usb", version="0.6.0",
                         lenses={"Задняя": {"zoom": [0.6, 10]}}),
            "192.168.42.129",
        )
        self.assertEqual(phone.id, "p1")
        self.assertEqual(phone.link, "usb")
        self.assertEqual(phone.address, "192.168.42.129")
        self.assertEqual(phone.protocol, 2)
        self.assertEqual(zoom_range(phone, "Задняя"), (0.6, 10.0))
        self.assertEqual(zoom_range(phone, "Фронтальная"), (1.0, 10.0))

    def test_reads_a_version_1_answer_and_ignores_noise(self) -> None:
        phone = parse_reply(b"LUMALINK-CAMERA-1", "192.168.42.129")
        self.assertEqual((phone.link, phone.protocol), ("usb", 1))
        self.assertIsNone(parse_reply(b"LUMALINK-CAMERA-2 not json", "10.0.0.2"))
        self.assertIsNone(parse_reply(camera_reply(name="no id"), "10.0.0.2"))
        self.assertIsNone(parse_reply(b"hello", "10.0.0.2"))

    def test_bad_zoom_ranges_fall_back(self) -> None:
        for zoom in ([0, 10], [3, 2], ["a", 1], None):
            phone = Phone(id="p", name="", link="wifi", lenses={"Задняя": {"zoom": zoom}})
            self.assertEqual(zoom_range(phone, "Задняя"), (1.0, 10.0))
        self.assertEqual(zoom_range(None, "Задняя"), (1.0, 10.0))

    def test_reads_ready_adb_devices(self) -> None:
        output = (
            "List of devices attached\n"
            "16be9296  device usb:1-1 product:miatoll model:Redmi_Note_9_Pro device:miatoll transport_id:2\n"
            "abcd      unauthorized usb:1-2 transport_id:3\n"
        )
        (phone,) = adb_phones(output)
        self.assertEqual((phone.serial, phone.name, phone.link), ("16be9296", "Redmi Note 9 Pro", "adb"))

    def test_sorts_by_name_then_usb_before_wifi(self) -> None:
        listed = sort_phones([
            Phone(id="b", name="Pixel", link="wifi", address="10.0.0.3"),
            Phone(id="a", name="Redmi", link="wifi", address="10.0.0.2"),
            Phone(id="a", name="Redmi", link="usb", address="192.168.42.129"),
            Phone(id="adb:s", name="Redmi", link="adb", serial="s"),
        ])
        self.assertEqual([(p.name, p.link) for p in listed],
                         [("Pixel", "wifi"), ("Redmi", "usb"), ("Redmi", "adb"), ("Redmi", "wifi")])


class FinderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.changes: list[list[Phone]] = []
        self.finder: PhoneFinder | None = None

    def tearDown(self) -> None:
        if self.finder is not None:
            self.finder.stop()

    def start(self, port: int, adb_output: str | None = None) -> None:
        self.finder = PhoneFinder(
            {"id": "computer-1", "name": "Studio Mac"},
            self.changes.append,
            port=port,
            targets=lambda: {"127.0.0.1": "127.0.0.1"},
            adb_devices=lambda: adb_output,
        )
        self.finder.start()

    def wait_for(self, condition, timeout: float = 4.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.05)
        self.fail(f"not reached; changes: {self.changes}")

    def test_lists_a_version_2_phone_once_and_says_who_is_asking(self) -> None:
        phone = FakePhone({"id": "p1", "name": "Redmi", "link": "wifi", "version": "0.6.0"})
        self.addCleanup(phone.close)
        self.start(phone.port)
        self.wait_for(lambda: self.changes and self.changes[-1])
        (found,) = self.changes[-1]
        self.assertEqual((found.id, found.name, found.link, found.protocol), ("p1", "Redmi", "wifi", 2))
        probe = next(p for p in phone.probes if p.startswith(b"LUMALINK-DISCOVER-2 "))
        self.assertEqual(json.loads(probe.split(b" ", 1)[1]), {"id": "computer-1", "name": "Studio Mac"})
        self.assertIn(b"LUMALINK-DISCOVER-1", phone.probes)

    def test_a_version_2_phone_over_usb_is_not_listed_twice(self) -> None:
        phone = FakePhone({"id": "p1", "name": "Redmi", "link": "usb"})
        self.addCleanup(phone.close)
        self.start(phone.port)
        self.wait_for(lambda: self.changes and self.changes[-1] and self.changes[-1][0].protocol == 2)
        time.sleep(1.5)  # more probes of both versions
        self.assertEqual([(p.link, p.protocol) for p in self.finder.phones], [("usb", 2)])

    def test_lists_a_version_1_phone(self) -> None:
        phone = FakePhone(None)
        self.addCleanup(phone.close)
        self.start(phone.port)
        self.wait_for(lambda: self.changes and self.changes[-1])
        (found,) = self.changes[-1]
        self.assertEqual((found.link, found.protocol, found.address), ("usb", 1, "127.0.0.1"))

    def test_forgets_a_phone_that_went_away_and_lists_adb(self) -> None:
        phone = FakePhone({"id": "p1", "name": "Redmi", "link": "wifi"})
        self.start(phone.port, adb_output="List of devices attached\nserial1\tdevice model:Pixel_8\n")
        self.wait_for(lambda: self.finder.phones and len(self.finder.phones) == 2)
        phone.close()
        self.wait_for(lambda: [p.link for p in self.finder.phones] == ["adb"], timeout=phones.FORGET_AFTER + 3)


if __name__ == "__main__":
    unittest.main()
