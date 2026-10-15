from __future__ import annotations

import ctypes
import ctypes.util
import errno
import json
import os
import http.client
import ipaddress
import select
import socket
import struct
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from typing import BinaryIO

from PIL import Image

from camera_studio.adb import AdbForward, open_adb_forward


HWACCEL_VARIABLE = "LUMALINK_HWACCEL"
HWACCEL_DEVICES = {"darwin": ("videotoolbox",), "win32": ("d3d11va", "dxva2")}
USB_PROTOCOL_MAGIC = b"LUMAAVC1"
USB_FRAME_ACK = b"\x06"
USB_DISCOVERY_PORT = 8766
USB_DISCOVERY_REQUEST = b"LUMALINK-DISCOVER-1"
USB_DISCOVERY_REPLY = b"LUMALINK-CAMERA-1"
STUDIO_DISCOVERY_PORT = 8767
STUDIO_DISCOVERY_REQUEST = b"LUMALINK-FIND-STUDIO-1"
STUDIO_DISCOVERY_REPLY = b"LUMALINK-STUDIO-1"
MAX_FRAME_SIZE = 4_000_000
LAN_PROBE_PORT = 9
WSAEACCES = 10013


def local_network_blocked(
    local_addresses: Iterable[str] | None = None,
    wait: float = 0.1,
) -> bool:
    """Tell whether a firewall, typically a VPN kill switch, blocks the LAN.

    Such a filter rejects an outgoing connection at once with an access error,
    while an allowed connection is still pending after ``wait`` seconds.
    """
    if local_addresses is None:
        local_addresses = local_ipv4_addresses()
    probes: list[socket.socket] = []
    try:
        for address in local_addresses:
            local = ipaddress.IPv4Address(address)
            if local.is_loopback or local.is_link_local or not local.is_private:
                continue
            network = ipaddress.IPv4Network(f"{address}/24", strict=False)
            neighbour = network.network_address + (2 if int(local) & 0xFF == 1 else 1)
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probes.append(probe)
            try:
                probe.setblocking(False)
                probe.bind((address, 0))
                probe.connect_ex((str(neighbour), LAN_PROBE_PORT))
            except OSError as error:
                if _is_access_denied(error.errno, getattr(error, "winerror", None)):
                    return True
        deadline = time.monotonic() + wait
        while probes and time.monotonic() < deadline:
            _, writable, failed = select.select([], probes, probes, 0.02)
            for probe in {*writable, *failed}:
                code = probe.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if _is_access_denied(code, code):
                    return True
                probes.remove(probe)
                probe.close()
    finally:
        for probe in probes:
            probe.close()
    return False


def _is_access_denied(error_number: int | None, windows_error: int | None) -> bool:
    return error_number == errno.EACCES or windows_error == WSAEACCES


def discover_usb_camera(
    stop: threading.Event,
    timeout: float = 6.0,
    port: int = USB_DISCOVERY_PORT,
    local_addresses: Iterable[str] | None = None,
    broadcast_address: str = "255.255.255.255",
) -> str:
    """Find the phone on a USB-tethering network and return its address.

    A probe is broadcast from every local IPv4 address; the phone answers
    only senders that reach it through its USB-tethering interface.
    """
    if local_addresses is None:
        targets = _usb_probe_targets(broadcast_address)
    else:
        targets = {address: broadcast_address for address in local_addresses}
    probes: dict[socket.socket, str] = {}
    try:
        for address, target in targets.items():
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                probe.bind((address, 0))
                probe.setblocking(False)
            except OSError:
                probe.close()
                continue
            probes[probe] = target

        deadline = time.monotonic() + timeout
        while probes and not stop.is_set() and time.monotonic() < deadline:
            for probe, target in probes.items():
                try:
                    probe.sendto(USB_DISCOVERY_REQUEST, (target, port))
                except OSError:
                    pass
            readable, _, _ = select.select(list(probes), [], [], 0.5)
            for probe in readable:
                try:
                    reply, sender = probe.recvfrom(64)
                except OSError:
                    continue
                if reply == USB_DISCOVERY_REPLY:
                    return sender[0]
    finally:
        for probe in probes:
            probe.close()
    raise ConnectionError(
        "Телефон не найден по USB. Подключите кабель, включите на телефоне "
        "«USB-модем» и нажмите «Начать трансляцию» в LumaLink Companion."
    )


def local_ipv4_addresses() -> list[str]:
    """This computer's IPv4 addresses on its networks, loopback excluded."""
    if sys.platform == "darwin":
        # The host name can take half a minute to resolve on macOS and does
        # not cover every interface, so ask the system; tunnels such as a VPN
        # have no broadcast address and are left out, like in USB discovery.
        return [
            address
            for address, broadcast in _macos_ipv4_interfaces()
            if broadcast and not ipaddress.IPv4Address(address).is_loopback
        ]
    return sorted(
        {
            result[4][0]
            for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
            if not ipaddress.IPv4Address(result[4][0]).is_loopback
        }
    )


def _usb_probe_targets(broadcast_address: str) -> dict[str, str]:
    """Map each local IPv4 address to where its USB discovery probe goes."""
    if sys.platform == "darwin":
        # macOS sends 255.255.255.255 out of the default route's interface
        # whatever address the socket is bound to, so each probe goes to the
        # broadcast address of its own network instead. The host name does not
        # resolve to every interface there either, so ask the system for them.
        # Tethering networks always broadcast; tunnels such as a VPN do not.
        return {
            address: broadcast
            for address, broadcast in _macos_ipv4_interfaces()
            if broadcast and not ipaddress.IPv4Address(address).is_loopback
        }
    return {
        result[4][0]: broadcast_address
        for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        if not ipaddress.IPv4Address(result[4][0]).is_loopback
    }


class _SockaddrIn(ctypes.Structure):
    # The BSD layout macOS uses: a length byte comes before the family.
    _fields_ = [
        ("sin_len", ctypes.c_uint8),
        ("sin_family", ctypes.c_uint8),
        ("sin_port", ctypes.c_uint16),
        ("sin_addr", ctypes.c_uint8 * 4),
    ]


class _Ifaddrs(ctypes.Structure):
    pass


_Ifaddrs._fields_ = [
    ("ifa_next", ctypes.POINTER(_Ifaddrs)),
    ("ifa_name", ctypes.c_char_p),
    ("ifa_flags", ctypes.c_uint),
    ("ifa_addr", ctypes.POINTER(_SockaddrIn)),
    ("ifa_netmask", ctypes.POINTER(_SockaddrIn)),
    ("ifa_dstaddr", ctypes.POINTER(_SockaddrIn)),
    ("ifa_data", ctypes.c_void_p),
]
_IFF_UP = 0x1
_IFF_BROADCAST = 0x2


def _macos_ipv4_interfaces() -> list[tuple[str, str | None]]:
    """The address and broadcast address of every IPv4 interface that is up."""
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    head = ctypes.POINTER(_Ifaddrs)()
    if libc.getifaddrs(ctypes.byref(head)) != 0:
        return []
    interfaces: list[tuple[str, str | None]] = []
    try:
        entry = head
        while entry:
            item = entry.contents
            entry = item.ifa_next
            if not item.ifa_addr or item.ifa_addr.contents.sin_family != socket.AF_INET:
                continue
            if not item.ifa_flags & _IFF_UP:
                continue
            address = socket.inet_ntoa(bytes(item.ifa_addr.contents.sin_addr))
            broadcast = None
            if item.ifa_flags & _IFF_BROADCAST and item.ifa_dstaddr:
                broadcast = socket.inet_ntoa(bytes(item.ifa_dstaddr.contents.sin_addr))
            interfaces.append((address, broadcast))
    finally:
        libc.freeifaddrs(head)
    return interfaces


def correct_stream_orientation(image: Image.Image) -> Image.Image:
    return image.transpose(Image.Transpose.ROTATE_180)


def encode_control_payload(command: dict[str, object]) -> bytes:
    return json.dumps(command, separators=(",", ":")).encode("utf-8")


def read_video_packet(stream: BinaryIO | _SocketReader) -> bytes:
    """Read one length-prefixed H.264 access unit."""
    header = _read_exactly(stream, 4, "Заголовок H.264-пакета получен не полностью")
    content_length = struct.unpack("!I", header)[0]
    if content_length <= 0 or content_length > MAX_FRAME_SIZE:
        raise ValueError("Недопустимый размер H.264-пакета")

    return _read_exactly(stream, content_length, "H.264-пакет получен не полностью")


class _SocketReader:
    """Unbuffered ``read`` over a socket.

    Unlike ``makefile()``, it does not keep the socket open after ``close()``,
    so closing from another thread interrupts a blocked read immediately.
    """

    def __init__(self, connection: socket.socket) -> None:
        self._connection = connection

    def read(self, size: int) -> bytes:
        return self._connection.recv(size)


def _interrupt(connection: socket.socket) -> None:
    """Wake a reader blocked on the socket in another thread.

    ``close()`` alone is deferred while a ``makefile()`` object is still open,
    so the socket is shut down first. Windows wakes the reader only once the
    socket is also closed; on macOS closing it right after the shutdown can
    leave the reader waiting out its whole timeout, so there the owning
    thread closes it, as it does anyway.
    """
    try:
        connection.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    if sys.platform == "win32":
        connection.close()


def _read_exactly(
    stream: BinaryIO | _SocketReader,
    length: int,
    error_message: str,
) -> bytes:
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
        discovery_port: int | None = STUDIO_DISCOVERY_PORT,
    ) -> None:
        self._bind_host = host
        self._port = port
        self._discovery_port = discovery_port
        self._discovery: socket.socket | None = None
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
        self._start_discovery()

    def _start_discovery(self) -> None:
        """Let phones on the Wi-Fi network find this computer without typing its IP."""
        if self._discovery_port is None:
            return
        responder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            responder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            responder.bind(("", self._discovery_port))
            responder.settimeout(0.5)
        except OSError:
            # Discovery is a convenience; the phone can still be given the IP.
            responder.close()
            return
        self._discovery = responder
        threading.Thread(
            target=self._answer_discovery,
            args=(responder,),
            daemon=True,
        ).start()

    def _answer_discovery(self, responder: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                request, sender = responder.recvfrom(64)
            except (socket.timeout, ConnectionResetError):
                continue
            except OSError:
                return
            if request != STUDIO_DISCOVERY_REQUEST or not self._serves(sender[0]):
                continue
            try:
                responder.sendto(STUDIO_DISCOVERY_REPLY, sender)
            except OSError:
                pass

    def _serves(self, peer: str) -> bool:
        """Whether ``peer`` is on the network this server listens on."""
        bind_address = ipaddress.IPv4Address(self._bind_host)
        if bind_address.is_unspecified:
            return True
        network = ipaddress.IPv4Network(f"{self._bind_host}/24", strict=False)
        return ipaddress.IPv4Address(peer) in network

    def stop(self) -> None:
        self._stop.set()
        discovery = self._discovery
        self._discovery = None
        if discovery is not None:
            discovery.close()
        client = self._client
        self._client = None
        if client is not None:
            _interrupt(client)
        listener = self._listener
        self._listener = None
        if listener is not None:
            listener.close()

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
        host: str | None,
        port: int,
        on_frame: Callable[[bytes], None],
        on_status: Callable[[str, bool], None],
        usb: bool,
        adb_fallback: bool = True,
    ) -> None:
        self._host = host
        self._port = port
        self._on_frame = on_frame
        self._on_status = on_status
        self._usb = usb
        self._adb_fallback = adb_fallback
        self._stop = threading.Event()
        self._connection: socket.socket | None = None
        self._adb_forward: AdbForward | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._send_lock = threading.Lock()
        self._pending_controls: deque[bytes] = deque(maxlen=1)

    def start(self) -> None:
        self._thread.start()

    def _connect_to_usb_phone(self) -> socket.socket:
        """Reach the phone over USB tethering, falling back to ADB.

        A VPN kill switch can block the tethering network entirely; ADB does
        not use the network stack beyond loopback, so it still gets through.
        """
        self._on_status("Ищем телефон по USB…", False)
        try:
            host = discover_usb_camera(self._stop, timeout=3.0)
            return socket.create_connection((host, self._port), timeout=5)
        except OSError as tethering_error:
            if self._stop.is_set() or not self._adb_fallback:
                raise
            self._on_status("Сеть USB-модема недоступна, пробуем ADB…", False)
            try:
                forward = open_adb_forward(self._port)
            except (OSError, subprocess.SubprocessError) as adb_error:
                if sys.platform == "darwin":
                    advice = (
                        "«USB-модем» Mac понимает не на всех телефонах: включите на "
                        "телефоне «Отладку по USB» — подключение пойдёт через неё"
                    )
                else:
                    advice = (
                        "Если сеть блокирует VPN, включите на телефоне "
                        "«Отладку по USB» — подключение пойдёт в обход VPN"
                    )
                raise ConnectionError(f"{tethering_error} {advice} ({adb_error}).") from adb_error
            self._adb_forward = forward
            return socket.create_connection(("127.0.0.1", forward.port), timeout=5)

    def stop(self) -> None:
        self._stop.set()
        connection = self._connection
        if connection is not None:
            _interrupt(connection)

    def send_control(self, command: dict[str, object]) -> None:
        connection = self._connection
        if connection is None:
            raise ConnectionError("USB-канал ещё не подключился")
        with self._send_lock:
            self._pending_controls.append(encode_control_payload(command))

    def _run(self) -> None:
        try:
            host = self._host
            if host is None:
                connection = self._connect_to_usb_phone()
            else:
                connection = socket.create_connection(
                    (host, self._port),
                    timeout=5,
                )
            self._connection = connection
            connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            connection.settimeout(5)
            stream: BinaryIO | _SocketReader = _SocketReader(connection)
            if self._usb:
                connection.sendall(USB_PROTOCOL_MAGIC)
                self._on_status(
                    "Получаем H.264-видео по USB (ADB)…"
                    if self._adb_forward is not None
                    else "Получаем H.264-видео по USB…",
                    False,
                )
            else:
                connection.sendall(
                    b"POST /publish HTTP/1.1\r\n"
                    + f"Host: {host}:{self._port}\r\n".encode("ascii")
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
            forward = self._adb_forward
            self._adb_forward = None
            if forward is not None:
                forward.close()


class H264Decoder:
    def __init__(self) -> None:
        try:
            import av
        except ImportError as error:
            raise RuntimeError(
                "Декодер H.264 недоступен. Переустановите LumaLink Studio."
            ) from error
        self._av = av
        self.accelerator = None
        self._codec = self._hardware_codec() if os.environ.get(HWACCEL_VARIABLE) == "1" else None
        if self._codec is None:
            self._codec = av.CodecContext.create("h264", "r")
            # Frame threading would hand frames over several frames late.
            self._codec.thread_count = 1

    def _hardware_codec(self):
        """A decoder on the GPU, or None. Off unless LUMALINK_HWACCEL=1.

        Measured at 1080p it saves about a millisecond of a 33 ms frame on a
        Mac (VideoToolbox) and on Windows (D3D11VA, DXVA2): copying the picture
        back from video memory costs most of the gain, so the software decoder
        stays the default and this is for checking a given computer.
        """
        from av.codec.hwaccel import HWAccel, hwdevices_available

        available = hwdevices_available()
        for device in HWACCEL_DEVICES.get(sys.platform, ()):
            if device not in available:
                continue
            try:
                codec = self._av.CodecContext.create(
                    "h264", "r", hwaccel=HWAccel(device_type=device, allow_software_fallback=True)
                )
            except Exception:
                continue
            self.accelerator = device
            return codec
        return None

    def decode(self, packet: bytes):
        try:
            decoded = self._codec.decode(self._av.Packet(packet))
        except self._av.error.InvalidDataError:
            return []
        return [
            frame.to_image()
            for frame in decoded
        ]
