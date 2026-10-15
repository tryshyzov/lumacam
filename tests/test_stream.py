import errno
import io
import socket
import struct
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from camera_studio import adb, stream
from camera_studio.stream import (
    STUDIO_DISCOVERY_REPLY,
    STUDIO_DISCOVERY_REQUEST,
    USB_DISCOVERY_REPLY,
    USB_DISCOVERY_REQUEST,
    USB_FRAME_ACK,
    USB_PROTOCOL_MAGIC,
    CameraStreamServer,
    H264FrameReceiver,
    correct_stream_orientation,
    discover_usb_camera,
    encode_control_payload,
    local_network_blocked,
    read_video_packet,
)


class H264TransportTests(unittest.TestCase):
    def test_corrects_inverted_stream_orientation(self) -> None:
        image = Image.new("RGB", (2, 2))
        image.putpixel((0, 0), (255, 0, 0))
        image.putpixel((1, 0), (0, 255, 0))
        image.putpixel((0, 1), (0, 0, 255))
        image.putpixel((1, 1), (255, 255, 0))

        corrected = correct_stream_orientation(image)

        self.assertEqual(corrected.getpixel((0, 0)), (255, 255, 0))
        self.assertEqual(corrected.getpixel((1, 0)), (0, 0, 255))
        self.assertEqual(corrected.getpixel((0, 1)), (0, 255, 0))
        self.assertEqual(corrected.getpixel((1, 1)), (255, 0, 0))

    def test_reads_length_prefixed_h264_access_unit(self) -> None:
        access_unit = b"\x00\x00\x00\x01\x65sample"
        packet = struct.pack("!I", len(access_unit)) + access_unit

        self.assertEqual(read_video_packet(io.BytesIO(packet)), access_unit)

    def test_reads_packet_when_stream_returns_short_reads(self) -> None:
        class ShortReader(io.BytesIO):
            def read(self, size: int = -1) -> bytes:
                return super().read(min(size, 2))

        access_unit = b"\x00\x00\x01\x41frame"
        packet = struct.pack("!I", len(access_unit)) + access_unit

        self.assertEqual(read_video_packet(ShortReader(packet)), access_unit)

    def test_rejects_incomplete_packet(self) -> None:
        with self.assertRaisesRegex(ConnectionError, "получен не полностью"):
            read_video_packet(io.BytesIO(struct.pack("!I", 4) + b"\x00\x01"))

    def test_usb_receiver_uses_h264_protocol_and_returns_control(self) -> None:
        access_unit = b"\x00\x00\x00\x01\x65keyframe"
        command = {"autofocus": False, "focus": 0.4, "resolution": "720p"}
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        received = threading.Event()
        frames: list[bytes] = []
        errors: list[str] = []
        handshakes: list[bytes] = []
        acknowledgements: list[bytes] = []
        control_payloads: list[bytes] = []
        receiver_holder: dict[str, H264FrameReceiver] = {}

        def serve() -> None:
            with listener:
                client, _ = listener.accept()
                with client:
                    stream = client.makefile("rb")
                    handshakes.append(stream.read(len(USB_PROTOCOL_MAGIC)))
                    client.sendall(struct.pack("!I", len(access_unit)) + access_unit)
                    acknowledgements.append(stream.read(5))
                    payload_length = struct.unpack("!I", acknowledgements[0][1:])[0]
                    control_payloads.append(stream.read(payload_length))
                    received.wait(2)

        server_thread = threading.Thread(target=serve, daemon=True)
        server_thread.start()
        receiver = H264FrameReceiver(
            "127.0.0.1",
            port,
            lambda frame: (
                frames.append(frame),
                receiver_holder["receiver"].send_control(command),
                received.set(),
            ),
            lambda message, is_error: errors.append(message) if is_error else None,
            usb=True,
        )
        receiver_holder["receiver"] = receiver
        receiver.start()
        try:
            self.assertTrue(received.wait(2))
            self.assertEqual(handshakes, [USB_PROTOCOL_MAGIC])
            self.assertEqual(frames, [access_unit])
            self.assertEqual(errors, [])
            server_thread.join(2)
            self.assertEqual(
                acknowledgements,
                [
                    USB_FRAME_ACK
                    + struct.pack("!I", len(encode_control_payload(command)))
                ],
            )
            self.assertEqual(control_payloads, [encode_control_payload(command)])
        finally:
            receiver.stop()

    def test_stopping_wakes_a_reader_blocked_on_the_socket(self) -> None:
        # Closing right after the shutdown left about one reader in ten
        # waiting out its timeout on macOS; repeat to catch such a race.
        for _ in range(50):
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            connection = socket.create_connection(listener.getsockname())
            peer, _ = listener.accept()
            connection.settimeout(5)
            reader = threading.Thread(target=lambda: connection.recv(4), daemon=True)
            reader.start()
            time.sleep(0.001)

            stream._interrupt(connection)
            reader.join(1)

            alive = reader.is_alive()
            for item in (connection, peer, listener):
                item.close()
            self.assertFalse(alive)

    def test_usb_receiver_falls_back_to_adb_when_tethering_is_blocked(self) -> None:
        access_unit = b"\x00\x00\x00\x01\x65keyframe"
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        received = threading.Event()
        stopped = threading.Event()
        frames: list[bytes] = []
        statuses: list[tuple[str, bool]] = []
        forwarded_ports: list[int] = []
        closed_forwards: list[bool] = []

        class FakeForward:
            port = listener.getsockname()[1]

            def close(self) -> None:
                closed_forwards.append(True)

        def serve() -> None:
            with listener:
                client, _ = listener.accept()
                with client:
                    stream = client.makefile("rb")
                    stream.read(len(USB_PROTOCOL_MAGIC))
                    client.sendall(struct.pack("!I", len(access_unit)) + access_unit)
                    stream.read(5)
                    stopped.wait(2)

        def open_forward(device_port: int) -> FakeForward:
            forwarded_ports.append(device_port)
            return FakeForward()

        threading.Thread(target=serve, daemon=True).start()
        receiver = H264FrameReceiver(
            None,
            8765,
            lambda frame: (frames.append(frame), received.set()),
            lambda message, is_error: statuses.append((message, is_error)),
            usb=True,
        )
        with (
            mock.patch(
                "camera_studio.stream.discover_usb_camera",
                side_effect=ConnectionError("Телефон не найден по USB."),
            ),
            mock.patch("camera_studio.stream.open_adb_forward", open_forward),
        ):
            receiver.start()
            try:
                self.assertTrue(received.wait(2))
            finally:
                receiver.stop()
                receiver._thread.join(2)
                stopped.set()

        self.assertEqual(frames, [access_unit])
        self.assertEqual(forwarded_ports, [8765])
        self.assertEqual(closed_forwards, [True])
        self.assertEqual([message for message, is_error in statuses if is_error], [])
        self.assertIn(("Получаем H.264-видео по USB (ADB)…", False), statuses)

    def test_detects_firewall_that_denies_local_network_connections(self) -> None:
        class DeniedSocket:
            def __init__(self, *_args: object) -> None:
                pass

            def setblocking(self, _flag: bool) -> None:
                pass

            def bind(self, _address: tuple[str, int]) -> None:
                pass

            def connect_ex(self, _address: tuple[str, int]) -> int:
                raise PermissionError(errno.EACCES, "blocked by firewall")

            def close(self) -> None:
                pass

        with mock.patch("camera_studio.stream.socket.socket", DeniedSocket):
            self.assertTrue(local_network_blocked(["192.168.1.20"]))

    def test_ignores_addresses_outside_the_local_network(self) -> None:
        self.assertFalse(local_network_blocked(["127.0.0.1", "169.254.10.20", "8.8.8.8"]))

    def test_discovers_phone_that_answers_usb_probe(self) -> None:
        responder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        responder.bind(("127.0.0.1", 0))
        responder.settimeout(2)
        requests: list[bytes] = []

        def respond() -> None:
            with responder:
                request, sender = responder.recvfrom(64)
                requests.append(request)
                responder.sendto(USB_DISCOVERY_REPLY, sender)

        responder_thread = threading.Thread(target=respond, daemon=True)
        responder_thread.start()

        address = discover_usb_camera(
            threading.Event(),
            timeout=2,
            port=responder.getsockname()[1],
            local_addresses=["127.0.0.1"],
            broadcast_address="127.0.0.1",
        )

        responder_thread.join(2)
        self.assertEqual(address, "127.0.0.1")
        self.assertEqual(requests, [USB_DISCOVERY_REQUEST])

    def test_reports_setup_help_when_no_phone_answers_usb_probe(self) -> None:
        silent = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        silent.bind(("127.0.0.1", 0))
        with silent:
            with self.assertRaisesRegex(ConnectionError, "USB-модем"):
                discover_usb_camera(
                    threading.Event(),
                    timeout=0.3,
                    port=silent.getsockname()[1],
                    local_addresses=["127.0.0.1"],
                    broadcast_address="127.0.0.1",
                )

    def test_macos_probes_each_network_on_its_own_broadcast_address(self) -> None:
        interfaces = [
            ("127.0.0.1", None),
            ("192.168.1.20", "192.168.1.255"),
            ("10.80.41.7", "10.80.41.255"),
            ("240.0.0.2", None),
        ]
        with (
            mock.patch.object(stream.sys, "platform", "darwin"),
            mock.patch.object(stream, "_macos_ipv4_interfaces", return_value=interfaces),
        ):
            targets = stream._usb_probe_targets("255.255.255.255")

        self.assertEqual(
            targets,
            {"192.168.1.20": "192.168.1.255", "10.80.41.7": "10.80.41.255"},
        )

    def test_uses_the_adb_inside_the_mac_app_when_none_is_installed(self) -> None:
        frameworks = Path("/Applications/LumaLink Studio.app/Contents/Frameworks")
        bundled = frameworks / "platform-tools" / "adb"
        with (
            mock.patch.object(adb.sys, "platform", "darwin"),
            mock.patch.object(adb.sys, "_MEIPASS", str(frameworks), create=True),
            mock.patch.object(adb.shutil, "which", return_value=None),
            mock.patch.object(Path, "is_file", lambda path: path == bundled),
        ):
            self.assertEqual(adb.find_adb(), bundled)

    def test_finds_android_studio_adb_on_macos_without_a_shell_path(self) -> None:
        sdk_adb = Path.home() / "Library" / "Android" / "sdk" / "platform-tools" / "adb"
        with (
            mock.patch.object(adb.sys, "platform", "darwin"),
            mock.patch.object(adb.shutil, "which", return_value=None),
            mock.patch.object(Path, "is_file", lambda path: path == sdk_adb),
        ):
            self.assertEqual(adb.find_adb(), sdk_adb)

    def test_macos_lists_its_own_networks_without_the_host_name(self) -> None:
        interfaces = [("127.0.0.1", None), ("192.168.1.20", "192.168.1.255"), ("240.0.0.2", None)]
        with (
            mock.patch.object(stream.sys, "platform", "darwin"),
            mock.patch.object(stream, "_macos_ipv4_interfaces", return_value=interfaces),
            mock.patch.object(stream.socket, "gethostname", side_effect=AssertionError),
        ):
            self.assertEqual(stream.local_ipv4_addresses(), ["192.168.1.20"])

    @unittest.skipUnless(sys.platform == "darwin", "reads macOS network interfaces")
    def test_lists_macos_network_interfaces(self) -> None:
        addresses = [address for address, _ in stream._macos_ipv4_interfaces()]

        self.assertIn("127.0.0.1", addresses)

    def test_wifi_server_receives_h264_and_returns_camera_control(self) -> None:
        access_unit = b"\x00\x00\x00\x01\x65keyframe"
        frame_received = threading.Event()
        received_frames: list[bytes] = []
        server = CameraStreamServer(
            lambda frame: (received_frames.append(frame), frame_received.set()),
            lambda _message, _is_error: None,
            host="127.0.0.1",
            port=0,
        )
        server.start()
        client = socket.create_connection(("127.0.0.1", server._port), timeout=2)
        client.settimeout(2)
        try:
            client.sendall(
                b"POST /publish HTTP/1.1\r\n"
                b"Content-Type: application/x-lumalink-h264\r\n\r\n"
            )
            response = client.makefile("rb")
            self.assertIn(b"200 OK", response.readline())
            while response.readline() not in (b"\r\n", b"\n"):
                pass

            command = {"zoom": 2.5, "fps": 30}
            server.send_control(command)
            client.sendall(struct.pack("!I", len(access_unit)) + access_unit)
            self.assertTrue(frame_received.wait(2))
            response_bytes = client.recv(5)
            self.assertEqual(
                response_bytes,
                USB_FRAME_ACK
                + struct.pack("!I", len(encode_control_payload(command))),
            )
            self.assertEqual(
                client.recv(len(encode_control_payload(command))),
                encode_control_payload(command),
            )
            self.assertEqual(received_frames, [access_unit])
        finally:
            client.close()
            server.stop()

    def test_stream_server_answers_wifi_discovery_from_its_network(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as free:
            free.bind(("127.0.0.1", 0))
            discovery_port = free.getsockname()[1]
        server = CameraStreamServer(
            lambda _frame: None,
            lambda _message, _is_error: None,
            host="127.0.0.1",
            port=0,
            discovery_port=discovery_port,
        )
        server.start()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as phone:
                phone.settimeout(2)
                phone.sendto(b"something else", ("127.0.0.1", discovery_port))
                phone.sendto(STUDIO_DISCOVERY_REQUEST, ("127.0.0.1", discovery_port))
                reply, _ = phone.recvfrom(64)
        finally:
            server.stop()

        self.assertEqual(reply, STUDIO_DISCOVERY_REPLY)
        self.assertTrue(server._serves("127.0.0.200"))
        self.assertFalse(server._serves("192.168.1.5"))

    def test_stream_server_rejects_unknown_endpoint(self) -> None:
        server = CameraStreamServer(
            lambda _frame: None,
            lambda _message, _is_error: None,
            host="127.0.0.1",
            port=0,
        )
        server.start()

        try:
            with socket.create_connection(("127.0.0.1", server._port), timeout=2) as client:
                client.sendall(b"POST /unknown HTTP/1.1\r\n\r\n")
                self.assertIn(b"404 Not Found", client.recv(256))
        finally:
            server.stop()

    def test_stream_server_rejects_non_h264_publishers(self) -> None:
        server = CameraStreamServer(
            lambda _frame: None,
            lambda _message, _is_error: None,
            host="127.0.0.1",
            port=0,
        )
        server.start()

        try:
            with socket.create_connection(("127.0.0.1", server._port), timeout=2) as client:
                client.sendall(
                    b"POST /publish HTTP/1.1\r\n"
                    b"Content-Type: multipart/x-mixed-replace\r\n\r\n"
                )
                self.assertIn(b"415 Unsupported Media Type", client.recv(256))
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
