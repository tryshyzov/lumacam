# Vendored binaries

`platform-tools/` holds ADB from Google's Android SDK Platform-Tools (Apache
License 2.0, see `platform-tools/NOTICE.txt`): `adb.exe` and its two DLLs on
Windows, the universal (Apple silicon and Intel) `adb` on macOS. The folder is
not committed; fill it before building the desktop app:

```powershell
.\scripts\fetch-platform-tools.ps1
```

```sh
scripts/fetch-platform-tools.sh   # macOS; scripts/build-macos.sh runs it when needed
```

`LumaLinkStudio.spec` and `LumaLinkStudio-macos.spec` bundle these files into
the app, and the build fails if they are missing.

LumaLink Studio uses ADB only as a fallback for the USB transport: when a VPN
kill switch blocks the phone's USB-tethering network, `adb forward` still
reaches the phone because it talks to it over USB directly. When running from
source, an `adb` found on `PATH` or installed through WinGet (Homebrew or
Android Studio on macOS) is used first. On macOS ADB is also the usual USB
route, since most phones' USB tethering has no macOS driver.
