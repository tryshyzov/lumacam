# LumaLink Camera

The virtual camera other applications see as **LumaLink Camera**. It is a
user-mode DirectShow source filter, not a kernel driver, so it needs no driver
signing. LumaLink Studio no longer depends on OBS Virtual Camera or Unity
Capture when it is installed.

It is a modified copy of the [Unity Capture](https://github.com/schellingb/UnityCapture)
filter (MIT, see [`LICENSE.txt`](./LICENSE.txt)). The changes:

- its own name, class IDs and shared-memory object names, so it coexists with
  an installed Unity Capture;
- an idle image (a camera glyph on gray) instead of the colored error
  patterns, shown whenever LumaLink Studio is not sending video;
- no configuration page, which only selected those patterns.

## How frames get in

The filter runs inside the application that opens the camera. While streaming
it owns a named mutex and a shared-memory block (`LumaLinkCam_*`, see
`shared.inl`). `camera_studio/lumalink_camera.py` writes RGBA frames into that
block. LumaLink Studio sends the same idle image itself while it runs without
a phone, so the picture does not change when the app starts or exits.

## Build

Visual Studio 2022 with the C++ desktop workload:

```powershell
msbuild virtualcam\LumaLinkCamera.vcxproj /p:Configuration=Release /p:Platform=x64
msbuild virtualcam\LumaLinkCamera.vcxproj /p:Configuration=Release /p:Platform=Win32
```

The DLLs are written to `virtualcam\Build\Release-LumaLinkCamera64` and
`...32`. Both are needed: 32-bit applications load the 32-bit one.

## Register

The installer registers both DLLs. To do it by hand from an elevated prompt:

```powershell
regsvr32 virtualcam\Build\Release-LumaLinkCamera64\LumaLinkCamera64.dll
C:\Windows\SysWOW64\regsvr32 virtualcam\Build\Release-LumaLinkCamera32\LumaLinkCamera32.dll
```

Add `/u` to unregister. Registration writes to `HKLM`, which is why the
installer asks for administrator rights.

## Limits

DirectShow cameras are visible to desktop applications such as Zoom, Discord,
Telegram, browsers and OBS. Microsoft Store applications and the Windows
Camera app use a different API and do not list them.
