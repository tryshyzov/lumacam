# Windows installer

The Inno Setup project builds `dist\installer\LumaLinkStudio-Setup-<version>.exe` from the already-built desktop executable and virtual camera:

```powershell
python -m PyInstaller --noconfirm --clean LumaLinkStudio.spec
msbuild virtualcam\LumaLinkCamera.vcxproj /p:Configuration=Release /p:Platform=x64
msbuild virtualcam\LumaLinkCamera.vcxproj /p:Configuration=Release /p:Platform=Win32
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" installer\LumaLinkStudio.iss
```

## What it installs

- `LumaLinkStudio.exe`, into `C:\Program Files\LumaLink Studio`.
- `LumaLinkCamera64.dll` and `LumaLinkCamera32.dll`, the **LumaLink Camera** DirectShow filter, registered with COM so that applications list it as a camera.

The installer requires administrator rights: registering a camera for every application writes to `HKEY_LOCAL_MACHINE`. It contains no kernel driver, driver certificate, or boot configuration changes, and never touches Secure Boot or test-signing.

Uninstalling unregisters the camera and removes the files. If an application has the camera open during an upgrade, the DLL is replaced on the next restart.

Both the desktop executable and setup are unsigned development artifacts. They can be signed with an Authenticode code-signing certificate for publisher identity and reputation; that is separate from driver signing, which this design does not need.

## Camera behavior

LumaLink Studio sends video to LumaLink Camera and needs no third-party camera software. While no phone is streaming, or while LumaLink Studio is closed, the camera shows an idle image. Without the installer (running from source or from the standalone executable) the app falls back to OBS Virtual Camera and then Unity Capture, if one of them is installed.

DirectShow cameras are listed by desktop applications; Microsoft Store applications and the Windows Camera app do not show them. Windows 11's Media Foundation virtual-camera API is not implemented.
