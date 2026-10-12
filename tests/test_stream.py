import io
import socket
import struct
import threading
import unittest

from PIL import Image

from camera_studio.stream import (
    USB_FRAME_ACK,
    USB_PROTOCOL_MAGIC,
    CameraStreamServer,
    H264FrameReceiver,
    correct_stream_orientation,
    encode_control_payload,
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
