"""ADB port forwarding used as the VPN-proof fallback for the USB transport.

ADB talks to the phone over USB directly and exposes it on the loopback
interface, so it keeps working when a VPN kill switch blocks every other
network path to the phone, including USB tethering.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ADB_FILES = ("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll")
# Keeps ADB from flashing a console window in the windowed executable.
_CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class AdbForward:
    adb: Path
    serial: str
    port: int

    def close(self) -> None:
        try:
            _run_adb(self.adb, "-s", self.serial, "forward", "--remove", f"tcp:{self.port}")
        except (OSError, subprocess.SubprocessError):
            pass


def open_adb_forward(device_port: int, serial: str | None = None) -> AdbForward:
    """Forward a free loopback port to ``device_port`` on the phone ``serial``,
    or on the first ready one."""
    adb = find_adb()
    if adb is None:
        raise ConnectionError("ADB не найден")
    result = _run_adb(adb, "devices")
    states = dict(
        line.split()[:2]
        for line in result.stdout.splitlines()[1:]
        if len(line.split()) >= 2
    )
    if serial is not None:
        if states.get(serial) != "device":
            raise ConnectionError("телефон отключён от USB или не разрешил отладку по USB")
    else:
        serial = next((name for name, state in states.items() if state == "device"), None)
    if serial is None:
        if "unauthorized" in states.values():
            raise ConnectionError(
                "подтвердите на экране телефона запрос «Разрешить отладку по USB»"
            )
        raise ConnectionError("на телефоне не включена «Отладка по USB»")
    result = _run_adb(adb, "-s", serial, "forward", "tcp:0", f"tcp:{device_port}")
    try:
        port = int(result.stdout.strip())
    except ValueError:
        raise ConnectionError(
            result.stderr.strip() or "не удалось настроить проброс порта"
        ) from None
    return AdbForward(adb, serial, port)


def find_adb() -> Path | None:
    adb_path = shutil.which("adb")
    if adb_path:
        return Path(adb_path)

    if sys.platform == "darwin":
        # An app opened from Finder does not get the shell's PATH.
        for executable in (
            Path("/opt/homebrew/bin/adb"),
            Path("/usr/local/bin/adb"),
            Path.home() / "Library" / "Android" / "sdk" / "platform-tools" / "adb",
        ):
            if executable.is_file():
                return executable
        return _bundled_macos_adb()

    if sys.platform.startswith("linux"):
        sdk = Path.home() / "Android" / "Sdk" / "platform-tools" / "adb"
        if sdk.is_file():
            return sdk
        return _bundled_linux_adb()

    package_root = (
        Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages"
    )
    for package in package_root.glob("Google.PlatformTools*"):
        executable = package / "platform-tools" / "adb.exe"
        if executable.is_file():
            return executable
    return _bundled_adb()


def _bundled_adb() -> Path | None:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root is None:
        executable = (
            Path(__file__).resolve().parents[1] / "vendor" / "platform-tools" / "adb.exe"
        )
        return executable if executable.is_file() else None

    source = Path(frozen_root) / "platform-tools"
    if not (source / "adb.exe").is_file():
        return None
    # The ADB server outlives the app, so it must not run from the one-file
    # bundle's temporary directory, which is deleted on exit.
    target = (
        Path(os.environ.get("LOCALAPPDATA", Path.home()))
        / "LumaLinkStudio"
        / "platform-tools"
    )
    try:
        target.mkdir(parents=True, exist_ok=True)
        for name in ADB_FILES:
            if not (target / name).is_file():
                shutil.copy2(source / name, target / name)
    except OSError:
        return None
    return target / "adb.exe"


def _bundled_macos_adb() -> Path | None:
    # Unlike the Windows one-file executable, the app bundle stays on disk, so
    # the ADB server, which outlives the app, can run from inside it.
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root is None:
        root = Path(__file__).resolve().parents[1] / "vendor"
    else:
        root = Path(frozen_root)
    executable = root / "platform-tools" / "adb"
    return executable if executable.is_file() else None


def _bundled_linux_adb() -> Path | None:
    frozen_root = getattr(sys, "_MEIPASS", None)
    root = Path(frozen_root) if frozen_root else Path(__file__).resolve().parents[1] / "vendor"
    source = root / "platform-tools" / "adb"
    if not source.is_file():
        return None
    if frozen_root is None:
        return source
    # The ADB server outlives the app, and an AppImage is unmounted when the
    # app exits, so the server runs from a copy in the user's data folder.
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    target = data_home / "LumaLinkStudio" / "platform-tools" / "adb"
    try:
        if not target.is_file() or target.stat().st_size != source.stat().st_size:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            target.chmod(0o755)
    except OSError:
        return None
    return target


def _run_adb(adb: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(adb), *arguments],
        capture_output=True,
        text=True,
        timeout=20,
        creationflags=_CREATION_FLAGS,
    )
