import json
import http.client
import socket
import struct
import threading
from collections import deque
from collections.abc import Callable
from typing import BinaryIO

from PIL import Image


USB_PROTOCOL_MAGIC = b"LUMAAVC1"
USB_FRAME_ACK = b"\x06"
MAX_FRAME_SIZE = 4_000_000


def correct_stream_orientation(image: Image.Image) -> Image.Image:
    return image.transpose(Image.Transpose.ROTATE_180)


def encode_control_payload(command: dict[str, object]) -> bytes:
    return json.dumps(command, separators=(",", ":")).encode("utf-8")


def read_video_packet(stream: BinaryIO) -> bytes:
    """Read one length-prefixed H.264 access unit."""
    header = _read_exactly(stream, 4, "Заголовок H.264-пакета получен не полностью")
    content_length = struct.unpack("!I", header)[0]
    if content_length <= 0 or content_length > MAX_FRAME_SIZE:
        raise ValueError("Недопустимый размер H.264-пакета")

    return _read_exactly(stream, content_length, "H.264-пакет получен не полностью")


def _read_exactly(stream: BinaryIO, length: int, error_message: str) -> bytes:
    data = bytearray()
    while len(data) < length:
        chunk = stream.read(length - len(data))
        if not chunk:
            if not data:
                raise ConnectionError("H.264-поток с телефона завершился")
            raise ConnectionError(error_message)
        data.extend(chunk)
    return bytes(data)


class CameraStreamServer:
    def __init__(
        self,
        on_frame: Callable[[bytes], None],
        on_status: Callable[[str, bool], None],
        host: str = "0.0.0.0",
        port: int = 8765,
    ) -> None:
        self._bind_host = host
        self._port = port
        self._on_frame = on_frame
        self._on_status = on_status
        self._stop = threading.Event()
        self._listener: socket.socket | None = None
        self._client: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._send_lock = threading.Lock()
        self._pending_controls: deque[bytes] = deque(maxlen=1)

    def start(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((self._bind_host, self._port))
            listener.listen(2)
            listener.settimeout(0.5)
        except OSError:
            listener.close()
            raise

        self._listener = listener
        self._port = listener.getsockname()[1]
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._listener is not None:
            self._listener.close()
            self._listener = None

    def send_control(self, command: dict[str, object]) -> None:
        client = self._client
        if client is None:
            raise ConnectionError("Телефон ещё не подключился")
        with self._send_lock:
            self._pending_controls.append(encode_control_payload(command))

    def _run(self) -> None:
        try:
            listener = self._listener
            if listener is None:
                return
            self._on_status(
                f"Ожидаем телефон на порту {self._port}…",
                False,
            )
            while not self._stop.is_set():
                try:
                    client, _ = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if not self._stop.is_set():
                        raise
                    return
                self._client = client
                self._serve_client(client)
                self._client = None
        except (OSError, ConnectionError, ValueError) as error:
            if not self._stop.is_set():
                self._on_status(str(error), True)

    def _serve_client(self, client: socket.socket) -> None:
        try:
            client.settimeout(5)
            request = client.makefile("rb")
            request_line = request.readline(8192)
            request_parts = request_line.decode("ascii", errors="replace").split()
            method = request_parts[0] if request_parts else ""
            target = request_parts[1] if len(request_parts) > 1 else ""
            content_type = ""
            while True:
                line = request.readline(8192)
                if not line or line in (b"\r\n", b"\n"):
                    break
                name, separator, value = line.decode(
                    "ascii",
                    errors="replace",
                ).partition(":")
                if separator and name.strip().lower() == "content-type":
                    content_type = value.strip()

            if method != "POST" or target.split("?", 1)[0] != "/publish":
                client.sendall(
                    b"HTTP/1.1 404 Not Found\r\n"
                    b"Content-Length: 0\r\nConnection: close\r\n\r\n"
                )
                return
            if content_type != "application/x-lumalink-h264":
                client.sendall(
                    b"HTTP/1.1 415 Unsupported Media Type\r\n"
                    b"Content-Length: 0\r\nConnection: close\r\n\r\n"
                )
                return

            with self._send_lock:
                client.sendall(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: application/x-lumalink-h264\r\n"
                    b"Connection: keep-alive\r\n\r\n"
                )
            self._on_status("Получаем H.264-видео…", False)
            while not self._stop.is_set():
                self._on_frame(read_video_packet(request))
                with self._send_lock:
                    control = self._pending_controls.pop() if self._pending_controls else b""
                    client.sendall(
                        USB_FRAME_ACK
                        + struct.pack("!I", len(control))
                        + control
                    )
        except (OSError, http.client.HTTPException, ConnectionError, ValueError) as error:
            if not self._stop.is_set():
                self._on_status(f"Передача с телефона прервана: {error}", True)
        finally:
            try:
                client.close()
            except OSError:
                pass


class H264FrameReceiver:
    def __init__(
        self,
        host: str,
        port: int,
        on_frame: Callable[[bytes], None],
        on_status: Callable[[str, bool], None],
        usb: bool,
    ) -> None:
        self._host = host
        self._port = port
        self._on_frame = on_frame
        self._on_status = on_status
        self._usb = usb
        self._stop = threading.Event()
        self._connection: socket.socket | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._send_lock = threading.Lock()
        self._pending_controls: deque[bytes] = deque(maxlen=1)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._connection is not None:
            self._connection.close()

    def send_control(self, command: dict[str, object]) -> None:
        connection = self._connection
        if connection is None:
            raise ConnectionError("USB-канал ещё не подключился")
        with self._send_lock:
            self._pending_controls.append(encode_control_payload(command))

    def _run(self) -> None:
        try:
            connection = socket.create_connection(
                (self._host, self._port),
                timeout=5,
            )
            self._connection = connection
            connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            connection.settimeout(5)
            stream = connection.makefile("rb")
            if self._usb:
                connection.sendall(USB_PROTOCOL_MAGIC)
                self._on_status("Получаем H.264-видео по USB…", False)
            else:
                connection.sendall(
                    b"POST /publish HTTP/1.1\r\n"
                    + f"Host: {self._host}:{self._port}\r\n".encode("ascii")
                    + b"Content-Type: application/x-lumalink-h264\r\n"
                    + b"Content-Length: 0\r\nConnection: keep-alive\r\n\r\n"
                )
                response = http.client.HTTPResponse(connection)
                response.begin()
                if response.status != 200:
                    raise ConnectionError(
                        f"Компьютер ответил HTTP {response.status}"
                    )
                if response.getheader("Content-Type") != "application/x-lumalink-h264":
                    raise ConnectionError("Компьютер не поддерживает H.264-поток")
                stream = response
                self._on_status("Получаем H.264-видео по Wi-Fi…", False)
            while not self._stop.is_set():
                packet = read_video_packet(stream)
                self._on_frame(packet)
                with self._send_lock:
                    pending = bytearray(USB_FRAME_ACK)
                    command = self._pending_controls.pop() if self._pending_controls else b""
                    pending.extend(struct.pack("!I", len(command)))
                    pending.extend(command)
                    connection.sendall(pending)
        except (OSError, ConnectionError, ValueError) as error:
            if not self._stop.is_set():
                self._on_status(f"Передача H.264 прервана: {error}", True)
        finally:
            connection = self._connection
            self._connection = None
            if connection is not None:
                connection.close()

class H264Decoder:
    def __init__(self) -> None:
        try:
            import av
        except ImportError as error:
            raise RuntimeError(
                "Декодер H.264 недоступен. Переустановите LumaLink Studio."
            ) from error
        self._av = av
        self._codec = av.CodecContext.create("h264", "r")
        self._codec.thread_count = 1

    def decode(self, packet: bytes):
        try:
            decoded = self._codec.decode(self._av.Packet(packet))
        except self._av.error.InvalidDataError:
            return []
        return [
            frame.to_image()
            for frame in decoded
        ]
