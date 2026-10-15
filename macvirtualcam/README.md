# LumaLink Camera for macOS

The macOS counterpart of [`virtualcam/`](../virtualcam/): a camera other
applications (Zoom, Telegram, browsers, OBS) see as **LumaLink Camera**. It is
a Core Media I/O camera extension, the kind of system extension macOS 12.3+
uses for virtual cameras; it runs in its own process, started by macOS, not
inside the applications that open it.

## Parts

- `Extension/`: the extension. Its device has two streams: a source that
  applications read, and a sink LumaLink Studio writes into. The source sends
  the newest frame from the sink, or the idle image (a camera glyph on gray,
  the same as on Windows) when none came for a second.
- `Helper/`: `lumalink-camera`, a command-line helper:
  - `install` / `uninstall` ask macOS to activate or remove the extension;
  - `status` exits 0 when the camera is present;
  - `list` prints the names of all cameras;
  - `feed` reads raw 1280×720 RGB frames from stdin and queues them into the
    sink. `camera_studio/mac_camera.py` runs it while LumaLink Studio streams.
- `Shared/CameraConfig.swift`: the name, IDs and frame format both agree on.

## Build

```sh
macvirtualcam/build.sh
```

It needs the Command Line Tools or Xcode. `scripts/build-macos.sh` runs it and
puts the extension in `LumaLink Studio.app/Contents/Library/SystemExtensions`
and the helper in `Contents/MacOS`: macOS lets only the app that carries a
system extension install it, and treats executables in `Contents/MacOS` as
that app.

## Signing — required to install

macOS installs a camera extension only from an app signed by an
[Apple Developer Program](https://developer.apple.com/programs/) team with the
`com.apple.developer.system-extension.install` entitlement, and only when the
app is in `/Applications`. A build without it gets
`Missing entitlement com.apple.developer.system-extension.install` from
`lumalink-camera install`; LumaLink Studio then keeps using OBS Virtual
Camera.

With a team:

1. Register the app ID `com.lumalink.studio` with the System Extension
   capability and the extension ID `com.lumalink.studio.camera` with the App
   Groups capability (group `<TEAM_ID>.com.lumalink.studio.camera`).
2. Create a Developer ID provisioning profile for `com.lumalink.studio`.
3. Build:

   ```sh
   MACOS_TEAM_ID=ABCDE12345 \
   MACOS_SIGN_IDENTITY="Developer ID Application: Name (ABCDE12345)" \
   MACOS_PROVISIONING_PROFILE=path/to/profile.provisionprofile \
   scripts/build-macos.sh
   ```

4. Notarize the disk image (`xcrun notarytool submit … --wait`, then
   `xcrun stapler staple`).
5. Copy the app to `/Applications`, run
   `"/Applications/LumaLink Studio.app/Contents/MacOS/lumalink-camera" install`,
   and allow it in System Settings → General → Login Items & Extensions →
   Camera Extensions.

Not yet tried with a signed build: the extension and helper compile and the
helper runs, but the camera itself has not been installed and streamed.
