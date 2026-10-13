# Vendored binaries

`platform-tools/` holds `adb.exe` and its two DLLs from Google's Android SDK
Platform-Tools (Apache License 2.0, see `platform-tools/NOTICE.txt`). The folder
is not committed; fill it before building the desktop executable:

```powershell
.\scripts\fetch-platform-tools.ps1
```

`LumaLinkStudio.spec` bundles these files into the executable, and the build
fails if they are missing.

LumaLink Studio uses ADB only as a fallback for the USB transport: when a VPN
kill switch blocks the phone's USB-tethering network, `adb forward` still
reaches the phone because it talks to it over USB directly. When running from
source, an `adb` found on `PATH` or installed through WinGet is used first.
