# LumaLink Studio

<p align="center"><img src="assets/LumaCam.png" alt="LumaLink Studio logo" width="240"></p>

Windows desktop prototype for using an Android phone as a camera. The iOS companion and production installer are not implemented yet.

## Run the desktop app

Requires Python 3.10+ with Tkinter and the packages in `requirements.txt`:

```powershell
python -m pip install -r requirements.txt
python main.py
```

Run the model tests with:

```powershell
python -m unittest discover -s tests -v
```

## Build a Windows executable

On Windows, install PyInstaller and build a standalone windowed executable:

```powershell
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --clean LumaLinkStudio.spec
```

The executable is written to `dist\LumaLinkStudio.exe`.

## Build a Windows installer

Install Inno Setup 6 and build the desktop executable first. From the project root, run:

```powershell
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" installer\LumaLinkStudio.iss
```

The setup package is written to `dist\installer`. It installs the desktop application without administrator rights or a LumaLink kernel driver. Virtual camera output requires OBS Studio with OBS Virtual Camera or a separately installed Unity Capture backend. The current desktop and setup executables are unsigned development artifacts, not public release binaries. See [`installer/README.md`](./installer/README.md) for details.

## Connect the Android camera

The `android-app` module is a native Kotlin/CameraX companion. It previews the camera and streams hardware-encoded H.264 access units to the desktop without an access code. USB uses a length-prefixed binary protocol with per-frame acknowledgement and camera-control messages over ADB forwarding; Wi-Fi uses the same video/control framing over a TCP connection.

Build and install from PowerShell with Android SDK and Java 17 configured:

```powershell
.\gradlew.bat :app:installDebug
```

To install the APK built locally and launch it:

```powershell
adb install -r android-app\build\outputs\apk\debug\app-debug.apk
adb shell am start -n com.lumalink.companion/.MainActivity
```

1. Keep the phone connected and open **LumaLink Companion**.
2. For **USB**, leave the phone address as `127.0.0.1`, tap **Начать трансляцию**, then select USB in LumaLink Studio and click **Подключить камеру**. The desktop sets up `adb forward` over authorized USB debugging.
3. For **Wi-Fi**, in LumaLink Studio select Wi-Fi and note the displayed computer IP. Enter that IP in the phone app, tap **Начать трансляцию**, then click **Подключить камеру** in LumaLink Studio. Both devices must be on the same trusted network; Windows Firewall may ask to allow the desktop app on private networks.
4. Stop the stream from either app when finished. There is no access code or encryption: any device that can reach the desktop on port 8765 can view the camera stream. Use Wi-Fi only on a trusted private network and stop streaming when not needed.

The desktop preview and live Android camera stream use length-prefixed H.264 access units over USB ADB forwarding or Wi-Fi. Android encodes with MediaCodec; Windows decodes with PyAV and corrects the stream's 180-degree orientation before displaying or forwarding frames. Each access unit is acknowledged before the next is sent, and the transport keeps only the latest pending access unit to limit queue latency. The desktop can request 480p, 720p, or 1080p analysis output and change focus/auto-focus, exposure, ISO, white balance, front/rear camera, zoom, and target FPS. The Android app applies supported controls via Camera2 and clamps unsupported settings to the device's reported capability ranges; actual stream resolution and cadence can be lower than requested on devices with limited camera modes or processing performance.

### Windows virtual camera

LumaLink does **not** install or use its former custom AVStream kernel driver. On Windows 10 and 11, the desktop app tries the separately installed **OBS Virtual Camera** first and **Unity Video Capture** second. If neither backend is installed and working, the app displays an error that names the required backends. Install OBS Studio (including its Virtual Camera component) or Unity Capture before starting virtual output. The LumaLink installer never enables test-signing or changes Secure Boot.

This dependency-based design avoids distributing a LumaLink kernel driver. It still relies on the third-party backend being properly installed and registered with Windows. Compatibility must be checked in the actual target application; some clients only enumerate cameras when they start.

Zero latency is not possible: camera exposure, encoding, transport, decoding, and desktop display each add delay.

## Product architecture

```text
Android companion (Kotlin / Camera2, CameraX) ─── session + video/control channel
                                                                    │
Planned iOS companion (Swift / AVFoundation) ──────────────────────┘
                                                                   ▼
                                              Windows desktop app (Python prototype)
                                                ├─ preview and settings UI
                                                ├─ USB / Wi-Fi transport adapters
                                                └─ OBS Virtual Camera / Unity Capture
                                                     │
                         Discord · Telegram · OBS · Zoom · Google Meet
```

OBS Virtual Camera or Unity Capture must be installed separately; LumaLink does not install camera drivers.

### Desktop (Windows first)

- The current prototype keeps the desktop UI, connection, decoding, and frame delivery in one Python application; a separate native session service is future work.
- Send frames through the `pyvirtualcam` OBS backend, falling back to Unity Capture. Never fall back to a physical camera.
- LumaLink does not install, register, update, or remove third-party camera devices.

### Mobile companions

- Android: implemented in Kotlin with CameraX/Camera2 controls. Device capabilities vary; this prototype does not yet report a complete capability map to the desktop UI.
- iOS: not implemented. A future Swift/AVFoundation companion must query each device/format's supported focus, exposure, white balance, lens, zoom, and frame-rate ranges before displaying controls.
- Both clients negotiate only capabilities actually supported by the selected camera. Unsupported controls remain unavailable rather than reporting false success.

### Transport

- The current prototype sends H.264 video without authentication or encryption over the local network for Wi-Fi. For USB, ADB forwards a desktop TCP connection to an Android abstract local socket; both transports use length-prefixed H.264 access units, per-frame acknowledgements, and a control channel. USB access relies on the user's authorized ADB debugging connection.
- OBS Virtual Camera and Unity Capture are third-party Windows camera backends and must be installed separately. LumaLink uses OBS first and Unity as fallback on both Windows 10 and Windows 11.
- A production transport can use an authenticated WebRTC session for video and control messages. Keep transport selection separate from encoding and the camera-control protocol.
- Validate latency, reconnect behavior, and network permissions independently on Android and iOS. Do not assume USB camera mode or USB tethering is available on every phone/OS combination.

### Compatibility and limitations

- A virtual camera depends on a camera backend installed in Windows; preview alone does not register a system-wide camera device.
- Camera controls and available lenses depend on the phone, selected lens, camera format, and OS APIs. In particular, an app must not promise manual ISO, focus, or white balance when the device does not expose them.
- Compatibility should be verified against each target application and Windows version; some video clients enumerate cameras only at startup.

## Implementation milestones

1. Windows desktop shell, shared session/control models, and capability-aware UI.
2. Android companion with hardware H.264 encoding and freshness-bounded USB/Wi-Fi framing over ADB forwarding or the local network.
3. Windows virtual-camera output through separately installed OBS Virtual Camera or Unity Capture.
4. USB transport polish and Wi-Fi latency/reconnect validation.
5. iOS companion, capability mapping, and USB/Wi-Fi interoperability.
6. Installer/signing, compatibility testing, and end-to-end device matrix.

The repository currently contains the desktop UI, Android camera preview, USB/Wi-Fi H.264 video transport, Android camera controls, and user-mode output through OBS Virtual Camera or Unity Capture. The former LumaLink AVStream driver is no longer built, installed, or used by the app; its source is retained only as a deprecated prototype. OBS and Unity have been tested through DirectShow on the development PC; compatibility in every target client, Wi-Fi latency/reconnect validation, truthful per-device control availability, iOS support, and signed production releases remain unfinished.
