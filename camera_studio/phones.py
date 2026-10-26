"""Finding the phones LumaLink Studio can connect to (protocol version 2).

A version 2 phone waits for a computer on its Wi-Fi, its USB tethering
network and loopback (see docs/protocol.md, "Version 2"). The finder
broadcasts a probe from every local IPv4 address about once a second and
lists the phones that answer, once per link, together with the phones ADB
sees over USB. Version 1 phones answer only over USB tethering and are
listed without a name.
"""

from __future__ import annotations

import hashlib
import json
import select
import socket
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from camera_studio.adb import _run_adb, find_adb
from camera_studio.stream import (
    USB_DISCOVERY_PORT,
    USB_DISCOVERY_REPLY,
    USB_DISCOVERY_REQUEST,
    _usb_probe_targets,
)

DISCOVERY_REQUEST_V2 = b"LUMALINK-DISCOVER-2"
DISCOVERY_REPLY_V2 = b"LUMALINK-CAMERA-2"
PROBE_INTERVAL = 1.0
# A phone that has not answered for this long has gone; three probes missed.
FORGET_AFTER = 3.5
ADB_INTERVAL = 3.0
# How often the list of local addresses is read again, for networks that
# come and go (USB tethering is switched on, Wi-Fi changes).
INTERFACES_INTERVAL = 5.0
LINKS = ("usb", "adb", "wifi")  # preferred first


@dataclass(frozen=True)
class Phone:
    """One phone on one link, as the device list shows it."""

    id: str
    name: str
    link: str  # "usb", "adb" or "wifi"
    address: str | None = None  # where to connect, for "usb" and "wifi"
    serial: str | None = None  # the ADB serial, for "adb"
    version: str | None = None  # None: a version 1 phone
    lenses: dict = field(default_factory=dict, compare=False, hash=False)
    tls: bool = False  # Wi-Fi connections go inside TLS ("Encrypted Wi-Fi")
    photo: bool = False  # takes full resolution photos on request ("Photos")

    @property
    def key(self) -> str:
        """Tells list entries apart: the same phone on two links is two entries."""
        return f"{self.link}:{self.address or self.serial or self.id}"

    @property
    def protocol(self) -> int:
        return 1 if self.version is None else 2


def parse_reply(reply: bytes, sender: str) -> Phone | None:
    """A phone from its answer to a probe, or None for anything else."""
    if reply == USB_DISCOVERY_REPLY:
        return Phone(id=f"v1:{sender}", name="", link="usb", address=sender)
    prefix = DISCOVERY_REPLY_V2 + b" "
    if not reply.startswith(prefix):
        return None
    try:
        info = json.loads(reply[len(prefix):].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(info, dict) or not isinstance(info.get("id"), str) or not info["id"]:
        return None
    link = info.get("link") if info.get("link") in ("usb", "wifi") else "wifi"
    lenses = info.get("lenses") if isinstance(info.get("lenses"), dict) else {}
    return Phone(
        id=info["id"],
        name=str(info.get("name") or ""),
        link=link,
        address=sender,
        version=str(info.get("version") or "2"),
        lenses=lenses,
        tls=info.get("tls") is True,
        photo=info.get("photo") is True,
    )


def fingerprint(certificate: bytes) -> str:
    """A phone's fingerprint: SHA-256 over its certificate's DER, in hex."""
    return hashlib.sha256(certificate).hexdigest()


def pairing_code(certificate: bytes, computer_id: str) -> str:
    """The six digits phone and computer both show while the user allows it."""
    digest = hashlib.sha256(certificate + computer_id.encode("utf-8")).digest()
    number = int.from_bytes(digest[:4], "big") % 1_000_000
    return f"{number // 1000:03d} {number % 1000:03d}"


def zoom_range(phone: Phone | None, lens: str) -> tuple[float, float]:
    """The zoom range the phone offers for ``lens``; 1 … 10 when it does not say."""
    try:
        low, high = (float(value) for value in phone.lenses[lens]["zoom"])  # type: ignore[union-attr]
    except (AttributeError, KeyError, TypeError, ValueError):
        return 1.0, 10.0
    if not 0 < low <= high:
        return 1.0, 10.0
    return low, high


def frame_rates(phone: Phone | None, lens: str, resolution: str) -> tuple[int, ...] | None:
    """The steady frame rates the lens really gives at ``resolution``, or None
    when the phone does not say."""
    try:
        listed = phone.lenses[lens]["fps"][resolution]  # type: ignore[union-attr]
        rates = tuple(sorted({int(rate) for rate in listed}))
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    return rates or None


def adb_phones(adb_devices_output: str) -> list[Phone]:
    """The ready phones in ``adb devices -l`` output."""
    phones = []
    for line in adb_devices_output.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2 or parts[1] != "device":
            continue
        details = dict(part.split(":", 1) for part in parts[2:] if ":" in part)
        name = details.get("model", "").replace("_", " ")
        phones.append(Phone(id=f"adb:{parts[0]}", name=name, link="adb", serial=parts[0]))
    return phones


class PhoneFinder:
    """Keeps a list of the phones around, calling ``on_change`` as it changes."""

    def __init__(
        self,
        computer: dict[str, str],
        on_change: Callable[[list[Phone]], None],
        use_adb: Callable[[], bool] = lambda: True,
        port: int = USB_DISCOVERY_PORT,
        targets: Callable[[], dict[str, str]] | None = None,
        adb_devices: Callable[[], str | None] | None = None,
    ) -> None:
        self._probe_v2 = DISCOVERY_REQUEST_V2 + b" " + json.dumps(computer).encode("utf-8")
        self._on_change = on_change
        self._use_adb = use_adb
        self._port = port
        self._targets = targets or (lambda: _usb_probe_targets("255.255.255.255"))
        self._adb_devices = adb_devices or _adb_devices
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="LumaLink phone finder", daemon=True)
        self._seen: dict[str, tuple[Phone, float]] = {}
        self._adb: list[Phone] = []
        self._listed: list[Phone] = []

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=2)

    @property
    def phones(self) -> list[Phone]:
        return list(self._listed)

    def _run(self) -> None:
        probes: dict[socket.socket, str] = {}
        interfaces_at = adb_at = probed_at = 0.0
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if now - interfaces_at >= INTERFACES_INTERVAL:
                    interfaces_at = now
                    probes = self._reopen(probes)
                if now - probed_at >= PROBE_INTERVAL:
                    probed_at = now
                    for probe, target in probes.items():
                        for request in (self._probe_v2, USB_DISCOVERY_REQUEST):
                            try:
                                probe.sendto(request, (target, self._port))
                            except OSError:
                                pass
                if now - adb_at >= ADB_INTERVAL:
                    adb_at = now
                    self._adb = adb_phones(self._adb_devices() or "") if self._use_adb() else []
                if probes:
                    readable, _, _ = select.select(list(probes), [], [], 0.25)
                else:
                    readable = []
                    self._stop.wait(0.25)
                for probe in readable:
                    try:
                        reply, sender = probe.recvfrom(16384)
                    except OSError:
                        continue
                    phone = parse_reply(reply, sender[0])
                    if phone is not None:
                        self._saw(phone, time.monotonic())
                self._publish(time.monotonic())
        finally:
            for probe in probes:
                probe.close()

    def _reopen(self, probes: dict[socket.socket, str]) -> dict[socket.socket, str]:
        try:
            wanted = self._targets()
        except OSError:
            wanted = {}
        current = {probe.getsockname()[0]: probe for probe in probes}
        kept: dict[socket.socket, str] = {}
        for address, probe in current.items():
            if address in wanted:
                kept[probe] = wanted[address]
            else:
                probe.close()
        for address, target in wanted.items():
            if address in current:
                continue
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                probe.bind((address, 0))
                probe.setblocking(False)
            except OSError:
                probe.close()
                continue
            kept[probe] = target
        return kept

    def _saw(self, phone: Phone, now: float) -> None:
        # A version 2 phone answers the version 1 probe too (over USB), so a
        # version 1 answer from an address that gave a version 2 one is the
        # same phone; a version 2 answer replaces a version 1 entry.
        if phone.protocol == 1 and any(
            seen.address == phone.address and seen.protocol == 2 for seen, _ in self._seen.values()
        ):
            return
        if phone.protocol == 2:
            for key, (seen, _) in list(self._seen.items()):
                if seen.address == phone.address and seen.protocol == 1:
                    del self._seen[key]
        self._seen[phone.key] = (phone, now)

    def _publish(self, now: float) -> None:
        for key, (_, seen_at) in list(self._seen.items()):
            if now - seen_at > FORGET_AFTER:
                del self._seen[key]
        phones = sort_phones([phone for phone, _ in self._seen.values()] + self._adb)
        if [(p.key, p.name, p.id) for p in phones] != [(p.key, p.name, p.id) for p in self._listed]:
            self._listed = phones
            self._on_change(list(phones))


def sort_phones(phones: Iterable[Phone]) -> list[Phone]:
    return sorted(phones, key=lambda phone: (phone.name.casefold(), LINKS.index(phone.link), phone.key))


def _adb_devices() -> str | None:
    adb = find_adb()
    if adb is None:
        return None
    try:
        return _run_adb(adb, "devices", "-l").stdout
    except (OSError, subprocess.SubprocessError):
        return None
