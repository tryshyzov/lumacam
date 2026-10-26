import json
import socket
import struct
import threading
import unittest
from unittest import mock

from camera_studio import stream
from camera_studio.phones import Phone
from camera_studio.stream import H264FrameReceiver, PhoneRefused

ACCESS_UNIT = b"\x00\x00\x00\x01\x09\x10"


def message(payload: dict) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    return struct.pack("!I", len(body)) + body


def read_exactly(client: socket.socket, length: int) -> bytes:
    data = b""
    while len(data) < length:
        chunk = client.recv(length - len(data))
        if not chunk:
            raise ConnectionError("closed")
        data += chunk
    return data


class FakePhoneServer:
    """A phone's TCP 8765 on loopback, answering each connection with ``script``."""

    def __init__(self, *scripts) -> None:
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.port = self.listener.getsockname()[1]
        self.scripts = list(scripts)
        self.seen: list[bytes] = []
        self.hellos: list[dict] = []
        self.done = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        for script in self.scripts:
            client, _ = self.listener.accept()
            with client:
                try:
                    script(self, client)
                except (ConnectionError, OSError):
                    pass
        self.done.set()

    def close(self) -> None:
        self.listener.close()


def version_2(*statuses: dict, frame: bool = True):
    def script(server: FakePhoneServer, client: socket.socket) -> None:
        magic = read_exactly(client, 8)
        server.seen.append(magic)
        length = struct.unpack("!I", read_exactly(client, 4))[0]
        server.hellos.append(json.loads(read_exactly(client, length)))
        for status in statuses:
            client.sendall(message(status))
        if frame:
            client.sendall(struct.pack("!I", len(ACCESS_UNIT)) + ACCESS_UNIT)
            read_exactly(client, 5)
            server.done.wait(2)
    return script


def version_1_only(server: FakePhoneServer, client: socket.socket) -> None:
    magic = read_exactly(client, 8)
    server.seen.append(magic)
    if magic != b"LUMAAVC1":
        return  # an old phone closes on anything else
    client.sendall(struct.pack("!I", len(ACCESS_UNIT)) + ACCESS_UNIT)
    read_exactly(client, 5)
    server.done.wait(2)


class HandshakeTests(unittest.TestCase):
    hello = {"id": "computer-1", "name": "Studio Mac", "token": ""}

    def receive(self, phone: Phone, port: int):
        frames, statuses, accepted, refused = [], [], [], []
        got = threading.Event()
        finished = threading.Event()
        receiver = H264FrameReceiver(
            None, port,
            lambda frame: (frames.append(frame), got.set()),
            lambda text, error: (statuses.append((text, error)), finished.set() if error else None),
            usb=False, phone=phone, hello=self.hello,
            on_accepted=accepted.append, on_refused=refused.append,
        )
        receiver.start()
        self.addCleanup(receiver.stop)
        return frames, statuses, accepted, refused, got, finished

    def test_waits_for_the_phone_to_allow_then_streams(self) -> None:
        ok = {"status": "ok", "token": "t-123", "camera": {"id": "p1"}, "settings": {"zoom": 1.0}}
        server = FakePhoneServer(version_2({"status": "waiting"}, ok))
        self.addCleanup(server.close)
        phone = Phone(id="p1", name="Redmi", link="wifi", address="127.0.0.1", version="0.6.0")
        frames, statuses, accepted, refused, got, _ = self.receive(phone, server.port)
        self.assertTrue(got.wait(3))
        self.assertEqual(server.seen, [b"LUMAAVC2"])
        self.assertEqual(server.hellos, [self.hello])
        self.assertEqual(frames, [ACCESS_UNIT])
        self.assertEqual(accepted[0]["token"], "t-123")
        self.assertIn(("Разрешите подключение на экране телефона", False), statuses)
        self.assertEqual(refused, [])

    def test_reports_a_refusal_with_its_reason(self) -> None:
        for status, reason, text in (
            ({"status": "denied", "reason": "timeout"}, "timeout", "не ответили"),
            ({"status": "denied"}, None, "не разрешил"),
            ({"status": "busy"}, None, "другой компьютер"),
        ):
            with self.subTest(status=status):
                server = FakePhoneServer(version_2(status, frame=False))
                self.addCleanup(server.close)
                phone = Phone(id="p1", name="Redmi", link="wifi", address="127.0.0.1", version="0.6.0")
                frames, statuses, accepted, refused, _, finished = self.receive(phone, server.port)
                self.assertTrue(finished.wait(3))
                self.assertEqual(frames, [])
                self.assertEqual(accepted, [])
                self.assertIsInstance(refused[0], PhoneRefused)
                self.assertEqual((refused[0].status, refused[0].reason), (status["status"], reason))
                self.assertIn(text, statuses[-1][0])

    def test_a_version_1_phone_over_adb_gets_the_old_handshake(self) -> None:
        server = FakePhoneServer(version_1_only, version_1_only)
        self.addCleanup(server.close)
        forward = mock.Mock(port=server.port)
        phone = Phone(id="adb:s1", name="Old phone", link="adb", serial="s1")
        with mock.patch.object(stream, "open_adb_forward", return_value=forward) as opened:
            frames, _, accepted, _, got, _ = self.receive(phone, 8765)
            self.assertTrue(got.wait(3))
        self.assertEqual(server.seen, [b"LUMAAVC2", b"LUMAAVC1"])
        self.assertEqual(frames, [ACCESS_UNIT])
        self.assertEqual(accepted, [])
        opened.assert_called_with(8765, "s1")

    def test_a_version_1_phone_over_usb_tethering(self) -> None:
        server = FakePhoneServer(version_1_only)
        self.addCleanup(server.close)
        phone = Phone(id="v1:127.0.0.1", name="", link="usb", address="127.0.0.1")
        frames, _, _, _, got, _ = self.receive(phone, server.port)
        self.assertTrue(got.wait(3))
        self.assertEqual(server.seen, [b"LUMAAVC1"])


if __name__ == "__main__":
    unittest.main()


FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures"


class EncryptedWifiTests(unittest.TestCase):
    """A phone that takes Wi-Fi connections inside TLS (docs/protocol.md)."""

    hello = {"id": "computer-1", "name": "Studio Mac", "token": "old-plain-token"}

    def tls_phone(self, *statuses: dict):
        import ssl

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(FIXTURES / "phone-cert.pem", FIXTURES / "phone-key.pem")

        def script(server: FakePhoneServer, client: socket.socket) -> None:
            server.seen.append(read_exactly(client, 8))
            secured = context.wrap_socket(client, server_side=True)
            version_2(*statuses)(server, secured)

        server = FakePhoneServer(script)
        self.addCleanup(server.close)
        return server

    def receive(self, port: int, known: str | None):
        from camera_studio.stream import H264FrameReceiver

        frames, statuses, accepted, refused = [], [], [], []
        got, finished = threading.Event(), threading.Event()
        phone = Phone(id="p1", name="Redmi", link="wifi", address="127.0.0.1", version="0.7.0", tls=True)
        receiver = H264FrameReceiver(
            None, port,
            lambda frame: (frames.append(frame), got.set()),
            lambda text, error: (statuses.append((text, error)), finished.set() if error else None),
            usb=False, phone=phone, hello=self.hello,
            on_accepted=accepted.append, on_refused=refused.append, known_fingerprint=known,
        )
        receiver.start()
        self.addCleanup(receiver.stop)
        return frames, statuses, accepted, refused, got, finished

    @staticmethod
    def certificate() -> bytes:
        import ssl

        return ssl.PEM_cert_to_DER_cert((FIXTURES / "phone-cert.pem").read_text())

    def test_first_pairing_shows_the_code_and_keeps_the_fingerprint(self) -> None:
        from camera_studio.phones import fingerprint, pairing_code

        server = self.tls_phone({"status": "waiting"}, {"status": "ok", "token": "t-new", "camera": {"id": "p1"}})
        frames, statuses, accepted, _, got, _ = self.receive(server.port, known=None)
        self.assertTrue(got.wait(5))
        self.assertEqual(server.seen, [b"LUMATLS1", b"LUMAAVC2"])
        # The token from an unencrypted pairing is not sent.
        self.assertEqual(server.hellos[0]["token"], "")
        code = pairing_code(self.certificate(), "computer-1")
        self.assertIn((f"Разрешите подключение на экране телефона. Код: {code}", False), statuses)
        self.assertEqual(accepted[0]["fingerprint"], fingerprint(self.certificate()))
        self.assertEqual(frames, [ACCESS_UNIT])

    def test_a_known_phone_gets_the_token(self) -> None:
        from camera_studio.phones import fingerprint

        server = self.tls_phone({"status": "ok", "camera": {"id": "p1"}})
        frames, _, accepted, _, got, _ = self.receive(server.port, known=fingerprint(self.certificate()))
        self.assertTrue(got.wait(5))
        self.assertEqual(server.hellos[0]["token"], "old-plain-token")
        self.assertEqual(len(accepted), 1)

    def test_another_device_posing_as_the_phone_gets_nothing(self) -> None:
        server = self.tls_phone({"status": "ok", "camera": {"id": "p1"}})
        frames, statuses, accepted, refused, _, finished = self.receive(server.port, known="00" * 32)
        self.assertTrue(finished.wait(5))
        self.assertEqual(server.hellos, [])
        self.assertEqual((refused[0].status, frames, accepted), ("impostor", [], []))
        self.assertIn("не тот телефон", statuses[-1][0])

    def test_a_known_phone_that_stops_encrypting_gets_nothing(self) -> None:
        from camera_studio.phones import fingerprint
        from camera_studio.stream import H264FrameReceiver

        # Answers like a phone without "tls": plain version 2.
        server = FakePhoneServer(version_2({"status": "ok", "camera": {"id": "p1"}}))
        self.addCleanup(server.close)
        refused, statuses = [], []
        finished = threading.Event()
        phone = Phone(id="p1", name="Redmi", link="wifi", address="127.0.0.1", version="0.6.0", tls=False)
        receiver = H264FrameReceiver(
            None, server.port, lambda frame: None,
            lambda text, error: (statuses.append(text), finished.set() if error else None),
            usb=False, phone=phone, hello=self.hello, on_refused=refused.append,
            known_fingerprint=fingerprint(self.certificate()),
        )
        receiver.start()
        self.addCleanup(receiver.stop)
        self.assertTrue(finished.wait(5))
        self.assertEqual(server.hellos, [])
        self.assertEqual(refused[0].status, "impostor")

    def test_pairing_code_has_six_digits_in_two_groups(self) -> None:
        from camera_studio.phones import pairing_code

        # A fixed vector the phone app's test checks too (PairingCodeTest.kt).
        self.assertEqual(pairing_code(b"certificate", "computer-1"), "300 879")
        self.assertRegex(pairing_code(b"certificate", "computer-1"), r"^\d{3} \d{3}$")
        self.assertNotEqual(pairing_code(b"certificate", "computer-1"), pairing_code(b"certificate", "computer-2"))
