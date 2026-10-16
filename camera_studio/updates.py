"""Finding a newer LumaLink Studio on GitHub and installing it from the app.

The app asks GitHub for the latest release, compares its tag with
``__version__`` and picks the file for this platform: the Windows installer,
or the macOS disk image for this processor. Downloading and installing
happen on the user's request; the app then quits so the new version can
take its place.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from camera_studio import __version__

REPOSITORY = "tryshyzov/lumacam"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
APP_NAME = "LumaLink Studio.app"
TIMEOUT = 15


@dataclass(frozen=True)
class Update:
    version: str
    file_name: str
    url: str
    size: int
    page: str


def parse_version(text: str) -> tuple[int, ...]:
    """``"v0.10.2"`` → ``(0, 10, 2)``; anything after the numbers is ignored."""
    match = re.match(r"v?(\d+(?:\.\d+)*)", text.strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else ()


def is_newer(candidate: str, current: str) -> bool:
    latest, installed = parse_version(candidate), parse_version(current)
    if not latest or not installed:
        return False
    width = max(len(latest), len(installed))
    return latest + (0,) * (width - len(latest)) > installed + (0,) * (width - len(installed))


def asset_pattern(system: str = sys.platform, machine: str | None = None) -> re.Pattern[str] | None:
    """Which release file installs on this computer, or None where none does."""
    if system == "win32":
        return re.compile(r"Setup.*\.exe$", re.IGNORECASE)
    if system == "darwin":
        machine = machine or platform.machine()
        processor = "apple-silicon" if machine == "arm64" else "intel"
        return re.compile(rf"-macos-{processor}\.dmg$", re.IGNORECASE)
    return None


def _fetch_latest() -> dict:
    request = urllib.request.Request(
        LATEST_RELEASE_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "LumaLink-Studio"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.load(response)


def check(
    current: str = __version__,
    fetch: Callable[[], dict] = _fetch_latest,
    pattern: re.Pattern[str] | None = None,
) -> Update | None:
    """The newer release for this computer, or None. Raises OSError when offline."""
    pattern = pattern or asset_pattern()
    if pattern is None:
        return None
    release = fetch()
    version = str(release.get("tag_name", "")).lstrip("v")
    if not is_newer(version, current):
        return None
    for asset in release.get("assets", []):
        if pattern.search(asset.get("name", "")):
            return Update(
                version=version,
                file_name=asset["name"],
                url=asset["browser_download_url"],
                size=int(asset.get("size", 0)),
                page=release.get("html_url", ""),
            )
    return None


def can_update_in_place() -> bool:
    """Only the packaged app updates itself; running from source does not."""
    return bool(getattr(sys, "frozen", False)) and asset_pattern() is not None


def download(
    update: Update,
    progress: Callable[[int, int], None],
    cancelled: threading.Event,
    directory: Path | None = None,
) -> Path:
    """Download ``update`` into a temporary folder, reporting bytes done and total."""
    folder = directory or Path(tempfile.mkdtemp(prefix="lumalink-update-"))
    target = folder / update.file_name
    request = urllib.request.Request(update.url, headers={"User-Agent": "LumaLink-Studio"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response, open(target, "wb") as file:
        total = int(response.headers.get("Content-Length") or update.size or 0)
        done = 0
        while chunk := response.read(256 * 1024):
            if cancelled.is_set():
                raise InterruptedError("Загрузка обновления отменена")
            file.write(chunk)
            done += len(chunk)
            progress(done, total)
    if update.size and target.stat().st_size != update.size:
        raise OSError("Обновление скачалось не полностью")
    return target


def install(path: Path) -> bool:
    """Start installing the downloaded file.

    True: the installer is waiting for the app to quit, so quit right away.
    False: the user declined (Windows' administrator prompt); keep running.
    """
    if sys.platform == "darwin":
        install_macos(path, current_app_bundle())
        return True
    if sys.platform == "win32":
        return install_windows(path)
    raise OSError("На этой системе программа не обновляется сама")


def current_app_bundle() -> Path:
    """``…/LumaLink Studio.app`` around the running executable (Contents/MacOS)."""
    bundle = Path(sys.executable).resolve().parents[2]
    if bundle.suffix != ".app":
        raise OSError("Программа запущена не из приложения LumaLink Studio")
    return bundle


def macos_install_script(image: Path, bundle: Path, process_id: int, relaunch: bool = True) -> str:
    """A shell script that swaps the app once this process has exited.

    It mounts the disk image, waits for the running app to quit, replaces the
    bundle with the one from the image and opens the new version. The image
    was downloaded by the app itself, so the copy carries no quarantine flag.
    """
    mount = tempfile.mkdtemp(prefix="lumalink-mount-")
    q = shlex.quote
    return f"""#!/bin/sh
set -e
hdiutil attach -nobrowse -readonly -noautoopen -mountpoint {q(mount)} {q(str(image))} >/dev/null
trap 'hdiutil detach -quiet {q(mount)} || true' EXIT
while kill -0 {process_id} 2>/dev/null; do sleep 0.2; done
staging={q(str(bundle))}.new
rm -rf "$staging"
ditto {q(mount + "/" + APP_NAME)} "$staging"
rm -rf {q(str(bundle))}
mv "$staging" {q(str(bundle))}
{"open " + q(str(bundle)) if relaunch else ""}
"""


def install_macos(image: Path, bundle: Path) -> None:
    if not os.access(bundle.parent, os.W_OK):
        raise PermissionError(
            f"Нет прав на запись в «{bundle.parent}». Скачайте обновление с сайта и установите вручную."
        )
    script = image.with_suffix(".sh")
    script.write_text(macos_install_script(image, bundle, os.getpid()))
    script.chmod(0o755)
    subprocess.Popen(
        ["/bin/sh", str(script)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


# Setup waits for this mutex to go away before it replaces the app; the name
# is repeated in installer/LumaLinkStudio.iss.
APP_MUTEX = "LumaLinkStudioRunning"
# Silent, without message boxes or a reboot; never closing other applications
# (they may hold the camera DLL, which is replaced on restart instead).
# /RELAUNCH makes Setup start the new version when it is done.
SETUP_ARGUMENTS = "/SILENT /SUPPRESSMSGBOXES /NORESTART /NOCLOSEAPPLICATIONS /RELAUNCH"
_SW_SHOWNORMAL = 1
_app_mutex: int | None = None


def install_windows(installer: Path, shell_execute: Callable[..., int] | None = None) -> bool:
    """Run the downloaded installer, which replaces the app once it has quit.

    Setup needs administrator rights, so it is started through ShellExecuteW
    with the "runas" verb, which returns once the user has answered the
    administrator prompt. False: the user declined, keep running. True: Setup
    is running and waits for the app's mutex (see hold_app_mutex) to go away,
    so quit right away; it installs and starts the new version.
    """
    if shell_execute is None:
        import ctypes

        function = ctypes.windll.shell32.ShellExecuteW
        function.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_int,
        ]
        function.restype = ctypes.c_void_p
        shell_execute = function
    _reset_bundle_environment()
    result = shell_execute(None, "runas", str(installer), SETUP_ARGUMENTS, None, _SW_SHOWNORMAL)
    # Values up to 32 are errors, among them a declined administrator prompt.
    return (result or 0) > 32


def _reset_bundle_environment() -> None:
    """Keep the packaged app's own variables away from what Setup starts.

    Setup, and the new version it starts, inherit this process's
    environment. The PyInstaller bootloader marks it with variables of its
    own; the new version's bootloader then takes itself for a child of this
    one and looks for this process's unpacked files, gone once it has quit
    ("Failed to load Python DLL").
    """
    for name in [name for name in os.environ if name.upper().startswith(("_PYI_", "_MEIPASS"))]:
        del os.environ[name]
    os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = "1"


def hold_app_mutex() -> None:
    """Hold the named mutex Setup waits on while the app runs (Windows only).

    Called once at startup; Windows releases it when the process exits.
    """
    global _app_mutex
    if sys.platform != "win32" or _app_mutex:
        return
    import ctypes

    create = ctypes.windll.kernel32.CreateMutexW
    create.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    create.restype = ctypes.c_void_p
    _app_mutex = create(None, False, APP_MUTEX)
