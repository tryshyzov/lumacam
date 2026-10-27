import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from camera_studio import updates


def release(tag: str, *names: str) -> dict:
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/tryshyzov/lumacam/releases/tag/{tag}",
        "assets": [
            {"name": name, "browser_download_url": f"https://example.com/{name}", "size": 10}
            for name in names
        ],
    }


ASSETS = (
    "LumaLinkStudio-Setup-0.5.0.exe",
    "LumaLinkStudio.exe",
    "LumaLinkStudio-0.5.0-macos-apple-silicon.dmg",
    "LumaLinkStudio-0.5.0-macos-intel.dmg",
    "LumaLinkStudio-0.5.0-x86_64.AppImage",
    "lumalink-studio_0.5.0_amd64.deb",
    "LumaLinkCompanion.apk",
)


class VersionTests(unittest.TestCase):
    def test_compares_versions_as_numbers(self) -> None:
        self.assertTrue(updates.is_newer("0.10.0", "0.9.9"))
        self.assertTrue(updates.is_newer("v0.5", "0.4.9"))
        self.assertFalse(updates.is_newer("0.4.0", "0.4.0"))
        self.assertFalse(updates.is_newer("0.4", "0.4.0"))
        self.assertFalse(updates.is_newer("0.3.9", "0.4.0"))

    def test_ignores_tags_that_are_not_versions(self) -> None:
        self.assertFalse(updates.is_newer("prototype", "0.4.0"))
        self.assertEqual(updates.parse_version("v1.2.3-beta"), (1, 2, 3))


class CheckTests(unittest.TestCase):
    def test_picks_the_file_for_each_platform(self) -> None:
        cases = {
            ("win32", "AMD64"): "LumaLinkStudio-Setup-0.5.0.exe",
            ("darwin", "arm64"): "LumaLinkStudio-0.5.0-macos-apple-silicon.dmg",
            ("darwin", "x86_64"): "LumaLinkStudio-0.5.0-macos-intel.dmg",
            ("linux", "x86_64"): "LumaLinkStudio-0.5.0-x86_64.AppImage",
        }
        for (system, machine), expected in cases.items():
            with self.subTest(system=system, machine=machine):
                update = updates.check(
                    "0.4.0",
                    fetch=lambda: release("v0.5.0", *ASSETS),
                    pattern=updates.asset_pattern(system, machine),
                )
                self.assertIsNotNone(update)
                self.assertEqual(update.file_name, expected)
                self.assertEqual(update.version, "0.5.0")

    def test_no_update_when_current_or_without_a_file_for_this_platform(self) -> None:
        pattern = updates.asset_pattern("darwin", "arm64")
        self.assertIsNone(updates.check("0.5.0", lambda: release("v0.5.0", *ASSETS), pattern))
        self.assertIsNone(
            updates.check("0.4.0", lambda: release("v0.5.0", "LumaLinkCompanion.apk"), pattern)
        )
        self.assertIsNone(updates.asset_pattern("freebsd", "amd64"))


class LinuxUpdateTests(unittest.TestCase):
    def test_only_the_appimage_updates_itself(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            appimage = Path(folder) / "LumaLinkStudio.AppImage"
            appimage.write_bytes(b"old")
            with (
                mock.patch.object(updates.sys, "platform", "linux"),
                mock.patch.object(updates.sys, "frozen", True, create=True),
            ):
                with mock.patch.dict(os.environ, {"APPIMAGE": str(appimage)}):
                    self.assertTrue(updates.can_update_in_place())
                with mock.patch.dict(os.environ, {}, clear=True):
                    # The .deb: still told about new versions, given a link.
                    self.assertTrue(updates.offers_updates())
                    self.assertFalse(updates.can_update_in_place())

    def test_replaces_the_appimage_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            current = Path(folder) / "LumaLinkStudio.AppImage"
            current.write_bytes(b"old")
            new = Path(folder) / "download.AppImage"
            new.write_bytes(b"new version")

            updates.install_appimage(new, current, relaunch=False)

            self.assertEqual(current.read_bytes(), b"new version")
            self.assertFalse(current.with_name(current.name + ".new").exists())
            if os.name == "posix":
                self.assertTrue(os.access(current, os.X_OK))


class DownloadTests(unittest.TestCase):
    def test_downloads_with_progress_and_checks_the_size(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.dmg"
            source.write_bytes(b"x" * 600_000)
            update = updates.Update("0.5.0", "Update.dmg", source.as_uri(), 600_000, "")
            seen: list[tuple[int, int]] = []
            target_dir = Path(folder) / "out"
            target_dir.mkdir()

            path = updates.download(update, lambda done, total: seen.append((done, total)),
                                    threading.Event(), target_dir)

            self.assertEqual(path.read_bytes(), source.read_bytes())
            self.assertEqual(seen[-1], (600_000, 600_000))

            short = updates.Update("0.5.0", "Short.dmg", source.as_uri(), 700_000, "")
            with self.assertRaises(OSError):
                updates.download(short, lambda *_: None, threading.Event(), target_dir)


@unittest.skipUnless(sys.platform == "darwin", "mounts a disk image")
class MacInstallTests(unittest.TestCase):
    def test_script_replaces_the_app_from_the_disk_image(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            new_app = root / "image" / updates.APP_NAME / "Contents"
            new_app.mkdir(parents=True)
            (new_app / "version").write_text("new")
            image = root / "update.dmg"
            subprocess.run(
                ["hdiutil", "create", "-quiet", "-srcfolder", str(root / "image"),
                 "-format", "UDZO", str(image)],
                check=True,
            )
            installed = root / "Applications" / updates.APP_NAME
            (installed / "Contents").mkdir(parents=True)
            (installed / "Contents" / "version").write_text("old")
            finished = subprocess.Popen(["true"])
            finished.wait()

            script = updates.macos_install_script(image, installed, finished.pid, relaunch=False)
            subprocess.run(["/bin/sh", "-c", script], check=True, timeout=60)

            self.assertEqual((installed / "Contents" / "version").read_text(), "new")
            self.assertFalse(Path(str(installed) + ".new").exists())


class WindowsInstallTests(unittest.TestCase):
    def test_runs_setup_elevated_and_silently(self) -> None:
        calls = []

        def shell_execute(*arguments):
            calls.append(arguments)
            return 42

        self.assertTrue(updates.install_windows(Path("C:/Temp/Setup.exe"), shell_execute))
        _, verb, file, parameters, _, _ = calls[0]
        self.assertEqual(verb, "runas")
        self.assertEqual(Path(file), Path("C:/Temp/Setup.exe"))
        for switch in ("/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOCLOSEAPPLICATIONS", "/RELAUNCH"):
            self.assertIn(switch, parameters.split())

    def test_setup_does_not_inherit_the_bundle_environment(self) -> None:
        seen = {}

        def shell_execute(*arguments):
            seen.update(os.environ)
            return 42

        bundle = {"_PYI_APPLICATION_HOME_DIR": "C:/Temp/_MEI1", "_PYI_PARENT_PROCESS_LEVEL": "1"}
        with mock.patch.dict(os.environ, bundle):
            updates.install_windows(Path("Setup.exe"), shell_execute)

        self.assertFalse([name for name in seen if name.startswith("_PYI_")])
        self.assertEqual(seen.get("PYINSTALLER_RESET_ENVIRONMENT"), "1")

    def test_keeps_running_when_the_administrator_prompt_is_declined(self) -> None:
        # ShellExecuteW answers SE_ERR_ACCESSDENIED (5) when the user says no.
        self.assertFalse(updates.install_windows(Path("Setup.exe"), lambda *_: 5))

    @unittest.skipUnless(sys.platform == "win32", "Windows mutex")
    def test_holds_the_mutex_setup_waits_for(self) -> None:
        import ctypes

        updates.hold_app_mutex()
        open_mutex = ctypes.windll.kernel32.OpenMutexW
        open_mutex.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
        open_mutex.restype = ctypes.c_void_p
        handle = open_mutex(0x00100000, False, updates.APP_MUTEX)  # SYNCHRONIZE
        self.assertTrue(handle)
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))


if __name__ == "__main__":
    unittest.main()
