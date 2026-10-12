# Windows installer

The Inno Setup project builds `dist\installer\LumaLinkStudio-Setup-0.1.0.exe` from the already-built desktop executable:

```powershell
python -m PyInstaller --noconfirm --clean LumaLinkStudio.spec
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" installer\LumaLinkStudio.iss
```

The installer is per-user and does not require administrator rights. It contains no kernel driver, driver certificate, or boot configuration changes. Virtual-camera output uses the OBS Virtual Camera backend when available, then falls back to Unity Capture. Users must install OBS Studio or Unity Capture separately. Both the desktop executable and setup are unsigned development artifacts and must not be presented as a production release.

## Camera backend behavior

LumaLink itself no longer installs or communicates with a custom kernel driver. The virtual camera output opens `OBS Virtual Camera` first and tries `Unity Video Capture` if OBS is absent or cannot start. A backend must already be installed and registered with Windows; the LumaLink installer never changes Secure Boot, test-signing, certificates, or camera-device drivers.

This user-mode design avoids the custom driver's Microsoft Hardware Developer Program signing requirement. OBS/Unity remain separate camera integrations, so their installation, device registration, and target-app compatibility still need to be present on the user's Windows 10 or 11 computer. Windows 11's Media Foundation virtual-camera API is not currently implemented as a standalone LumaLink backend.

The app and installer can optionally be signed with an Authenticode code-signing certificate for publisher identity and reputation. That is separate from driver signing; the current development builds are unsigned.
