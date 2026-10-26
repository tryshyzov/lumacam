# Phone ↔ desktop protocol

How a LumaLink phone app streams video to LumaLink Studio and receives camera
settings back. A new phone app (for example the iOS one) that follows this
document works with the existing desktop apps without changes on their side.

The reference implementations are `camera_studio/stream.py` (desktop) and
`android-app/src/main/java/com/lumalink/companion/` (`UsbFrameServer.kt`,
`H264Publisher.kt`). If this document and the code disagree, the code wins;
please fix the document.

There is no authentication or encryption. Both transports are meant for a USB
cable or a trusted local network.

## Overview

| | USB | Wi-Fi |
| --- | --- | --- |
| Network | The phone's USB tethering network, or `adb forward` | The local network both devices are on |
| Who listens on TCP 8765 | The phone | LumaLink Studio |
| Who connects | LumaLink Studio | The phone |
| Discovery | Studio broadcasts on UDP 8766, the phone answers | The phone broadcasts on UDP 8767, Studio answers |
| Handshake | Studio sends the 8 bytes `LUMAAVC1` | The phone sends an HTTP-style `POST /publish` |

After the handshake both transports carry the same frame stream.

## Frame stream

All integers are unsigned 32-bit big-endian.

The phone sends, for each video frame:

```
length   u32      number of bytes that follow, 1 … 4 000 000
data     bytes    one H.264 access unit in Annex B format (00 00 00 01 start codes)
```

The desktop answers **every** frame before the phone sends the next one:

```
ack      u8       always 0x06
length   u32      0 … 16 384; 0 means no settings change
command  bytes    UTF-8 JSON object with camera settings (see below), if length > 0
```

Rules the phone app must follow:

- **Send only the newest frame.** The phone keeps the latest encoded frame and
  sends it once the previous one is acknowledged; frames encoded in between
  are dropped. This bounds latency to one frame in flight.
- **Key frames carry the parameter sets.** Put the SPS and PPS NAL units in
  front of every key frame (IDR), so a receiver can start decoding at any key
  frame. The desktop creates a fresh decoder for each connection.
- **No B-frames**, and preferably a key frame at least once a second. Android
  uses `KEY_I_FRAME_INTERVAL = 1`, `KEY_MAX_B_FRAMES = 0` and low-latency mode.
- After a dropped frame the frames that depended on it no longer decode
  cleanly. A sender should resume with a key frame (request one from the
  encoder) instead of sending the broken frames.
- A new connection also starts with a key frame. The Android app holds back
  other frames after a gap and when a connection starts, asks the encoder for
  a key frame (at most every 300 ms) and sends it with SPS and PPS in front
  (`LatestFrame.kt`).
- **The picture size can change mid-stream.** The video follows the way the
  phone is held, so a quarter turn swaps width and height (1280×720 becomes
  720×1280). The phone then starts a new encoder, whose first frame is a key
  frame with the new SPS and PPS; the desktop decoder picks the size up from
  them. Receivers must not assume one size per connection; the desktop fits
  each picture into its preview and into the virtual camera's 1280×720 frame.

Orientation: the desktop rotates every decoded picture by 180° before showing
it. The Android app encodes the picture turned half a turn from the way the
phone is held (`HeldRotation.streamTarget`) to match. A new phone app has to
deliver the same orientation, that is, upside down relative to the scene, or
the desktop needs a per-client setting.

The desktop treats a connection as broken after 5 seconds without a frame.
The phone likewise gives up after 5 seconds without an acknowledgement, so a
computer that vanished without closing the connection is noticed.

## Camera settings

Sent by the desktop in the acknowledgement, as one JSON object with all keys
present. The phone applies what its camera supports and ignores the rest.

| Key | Type | Values |
| --- | --- | --- |
| `autofocus` | boolean | `true` = continuous autofocus |
| `focus` | number | `0` (far) … `1` (near); used when `autofocus` is `false` |
| `exposure` | number | `-2` … `2` EV |
| `iso` | string | `"Авто"` or a number as a string: `"100"`, `"200"`, `"400"`, `"800"`, `"1600"` |
| `whiteBalance` | string | `"Авто"`, `"Лампа"`, `"Люминесцентный"`, `"Дневной"`, `"Облачно"` |
| `lens` | string | `"Задняя"` (rear) or `"Фронтальная"` (front) |
| `zoom` | number | `1` … `10` (version 2: the lens's range from `lenses`, which may start below 1), clamped by the phone to what the lens supports; below 1 and at long zoom the phone picks the camera module itself |
| `fps` | integer | `30` or `60`; the phone clamps to `15` … `60` |
| `resolution` | string | `"480p"`, `"720p"` or `"1080p"`: the size the phone should encode |

The string values are **Russian words in every interface language**. They are
protocol tokens, not display text.

Example:

```json
{"autofocus":true,"focus":0.5,"exposure":0.0,"iso":"Авто","whiteBalance":"Авто","lens":"Задняя","zoom":1.0,"fps":30,"resolution":"720p"}
```

The desktop sends the full set right after the first frame of a connection
and again whenever the user changes a setting.

## USB

### Tethering

The user turns on USB tethering on the phone. The phone runs a TCP server on
port 8765 and a UDP responder on port 8766, and accepts peers only from the
subnet of its tethering interface (Android: interfaces named `rndis*`, `usb*`
or `ncm*`) or from loopback.

Discovery, done by the desktop:

1. From each local IPv4 address, send the ASCII bytes `LUMALINK-DISCOVER-1`
   as a UDP datagram to the broadcast address, port 8766, about twice a second.
2. The phone answers the sender with `LUMALINK-CAMERA-1`.
3. The desktop connects to the phone's address from that answer, TCP 8765.

The desktop gives up after 3 seconds and tries the ADB route below.

### ADB fallback

A VPN with a kill switch on the computer blocks the tethering network. The
desktop then runs `adb forward tcp:0 tcp:8765` and connects to the forwarded
port on `127.0.0.1`. The phone sees a loopback peer, which it accepts. This
needs USB debugging on the phone and is Android-only.

### Handshake

The desktop sends the 8 ASCII bytes `LUMAAVC1`. The phone checks them and
starts the frame stream; anything else closes the connection.

## Wi-Fi

LumaLink Studio listens on TCP 8765 on the address the user selected, and
answers discovery on UDP 8767.

Discovery, done by the phone when no IP address was typed:

1. Send the ASCII bytes `LUMALINK-FIND-STUDIO-1` to `255.255.255.255` and to
   the broadcast address of each network interface, UDP port 8767, every
   500 ms, for up to 6 seconds.
2. Studio answers the sender with `LUMALINK-STUDIO-1`, but only when the sender
   is on the same /24 network as the address Studio listens on.
3. The phone connects to the address that answered, TCP 8765.

Handshake, sent by the phone:

```
POST /publish HTTP/1.1
Host: <studio address>:8765
Content-Type: application/x-lumalink-h264
Content-Length: 0
Connection: keep-alive

```

Studio answers:

```
HTTP/1.1 200 OK
Content-Type: application/x-lumalink-h264
Connection: keep-alive

```

Then the frame stream starts on the same connection. Studio answers
`404 Not Found` for any other method or path and `415 Unsupported Media Type`
for any other content type, and closes the connection. The phone must check
both the status code and the content type of the answer.

## Version 2: phones that are always ready

Version 2 (phone app and LumaLink Studio 0.6 and later) drops the phone's
"Start streaming" button. While the app is open the phone waits for a
computer on every network it is on, the computer finds phones by itself,
lists them with the link they are on, and connects to the one the user
picked last. Over Wi-Fi a computer the phone does not know yet has to be
allowed on the phone once.

In version 2 **the computer always connects to the phone**, on USB and on
Wi-Fi alike. The phone listens on TCP 8765 and answers discovery on UDP 8766
only on IPv4, and only on its Wi-Fi, its USB tethering network and loopback:
never on the mobile network, where an IPv6 address can be reachable from the
internet. Over Wi-Fi it accepts only peers with private IPv4 addresses.

### Discovery

The computer sends, from each local IPv4 address to the broadcast address,
UDP port 8766, about once a second while it is looking for phones:

```
LUMALINK-DISCOVER-2 <json>
```

`<json>` describes the computer: `{"id": "<id>", "name": "<name>"}`, where
`id` is a random UUID the computer keeps, and `name` is its host name, for
the phone to show. It also keeps sending the version 1 probe
`LUMALINK-DISCOVER-1`, which old phones answer.

A version 2 phone answers the sender:

```
LUMALINK-CAMERA-2 <json>
```

```json
{
  "id": "6f1c…",
  "name": "Redmi Note 9 Pro",
  "link": "usb",
  "version": "0.6.0",
  "lenses": {
    "Задняя": {"zoom": [0.5, 10.0], "fps": {"480p": [30, 60], "720p": [30, 60], "1080p": [30]}},
    "Фронтальная": {"zoom": [1.0, 4.0], "fps": {"480p": [30], "720p": [30], "1080p": [30]}}
  }
}
```

| Key | Meaning |
| --- | --- |
| `id` | A random UUID the phone keeps, to recognise it on any link |
| `name` | What to show in the device list: the device name the user set, or the model |
| `link` | The interface the probe arrived on: `"usb"` (USB tethering or loopback, that is `adb forward`) or `"wifi"` (the phone's Wi-Fi network) |
| `version` | The phone app's version |
| `tls` | `true` when Wi-Fi connections must use TLS (see "Encrypted Wi-Fi") |
| `lenses` | For each lens token of the camera settings, the zoom range the phone offers (`zoom`) and the frame rates it can really deliver (`fps`, see below) |

`zoom` is `[minimum, maximum]` as a ratio to the main camera. A minimum below
1 means a wider view than the main camera, from an ultra-wide module, and
the phone switches camera modules by itself as the zoom changes. Phones
without a module the app may use report a minimum of 1. Some phones keep
their extra modules for their own camera apps (for example many Xiaomi
phones): there the range starts at 1 even if the phone has an ultra-wide.

How the Android app picks modules: by field of view (sensor width ÷ focal
length), not by focal length, because macro and depth modules have short
lenses on tiny sensors. A module's ratio is the main camera's field of view
divided by its own. An ultra-wide is a back module with a ratio of at most
0.8 that also delivers the requested resolution (which leaves out depth and
macro modules); a telephoto, one with a ratio of at least 1.6. With hysteresis, so a zoom near
the threshold does not rebind the camera back and forth: the ultra-wide
takes over at a requested zoom of 0.95 and below, and hands back at 1.05
and above (likewise around a telephoto's ratio). A switch rebinds the
camera and starts a new encoder, so the next frame is a key frame and the
size may change (see "The picture size can change mid-stream").

`fps` maps each resolution token of the camera settings to the steady frame
rates among 30 and 60 the lens really delivers at that size, for example
`{"480p": [30], "720p": [30, 60], "1080p": [30]}`. Studio offers 60 fps
only where it is listed, and without the key it offers both, as before.
Many phones film 60 fps in their own camera app but give other apps 30 at
most: they list `[30]` everywhere.

The phone answers `LUMALINK-DISCOVER-1` as before, only from its tethering
network, with `LUMALINK-CAMERA-1`.

The ADB route needs no discovery: the computer lists `adb devices`, forwards
TCP 8765 of each, and reads the same description from the handshake below,
where `link` is `"usb"`.

### Handshake

The computer connects to TCP 8765 and sends:

```
magic    8 bytes  "LUMAAVC2"
length   u32      size of the JSON that follows, at most 16 384
hello    bytes    {"id": "<computer id>", "name": "<computer name>", "token": "<token or empty>"}
```

The phone answers with one or more status messages, each:

```
length   u32      size of the JSON that follows, at most 16 384
status   bytes    {"status": "...", ...}
```

| `status` | Meaning | Then |
| --- | --- | --- |
| `"ok"` | Streaming starts. Carries `"camera"`, the description from discovery; `"settings"`, the camera settings in use (same keys as below), for the computer to set its controls; and `"token"` when the phone just issued one | The frame stream follows |
| `"waiting"` | The phone asks its user to allow this computer | Another status follows when they answer, or after 60 s |
| `"denied"` | The user refused, or did not answer in time. An optional `"reason"`: `"refused"`, `"timeout"` or `"cooldown"` (see below) | The phone closes the connection |
| `"busy"` | Another computer (another `id`) is streaming from this phone | The phone closes the connection |

When the `id` in the hello is the one the phone is streaming to now, the
phone closes that older connection and takes the new one: a computer whose
Wi-Fi dropped for a moment reconnects before the old connection times out.

The frame stream and the camera settings are the same as in version 1.

**Allowing a computer.** A peer on the phone's tethering network or on
loopback (USB) is always allowed. On any other network the phone checks the
`token`: when it is one the phone issued to this computer `id` earlier,
streaming starts. Otherwise the phone sends `"waiting"`, shows "Allow
<computer name> to use the camera?", and on "Allow" stores a new random token
for that `id` and sends it in `"ok"`. The computer keeps the token for that
phone `id` and sends it from then on. The phone's settings list the allowed
computers and can forget them. The phone asks about one computer at a time;
after a refusal it answers that `id` with `"denied"` (`"cooldown"`) for 30
seconds without asking again.

The token keeps other devices on the network from taking the camera without
the user noticing. It travels in plain text like the video, so it does not
protect against someone who can read the network's traffic. Loopback counts
as USB because `adb forward` arrives there, so an app on the phone itself
could also connect without asking; Android gives no way to tell adbd from
an app. LumaLink has to be open on the screen for that, and it shows that a
computer is connected.

A version 2 phone accepts `LUMAAVC1` only from its tethering network or
loopback, so old computers keep working over USB. Old computers cannot reach
a version 2 phone over Wi-Fi: the version 1 Wi-Fi direction (the phone
connecting to Studio) is gone from the phone. So that this does not fail
silently, a version 2 phone with no computer connected sends
`LUMALINK-FIND-STUDIO-1` about every 5 seconds; when a version 1 Studio
answers, the phone tells its user to update LumaLink Studio on the computer.
It does not connect to it, which would bypass allowing the computer.

### Encrypted Wi-Fi (phone app 0.7 and later)

Over Wi-Fi the token and the video used to travel in plain text, so anyone
who could read the network's traffic could watch the camera or take the
token. A phone that announces `"tls": true` in its discovery description
takes Wi-Fi connections only inside TLS; over USB nothing changes.

**The phone's key.** The phone makes an ECDSA P-256 key once, kept in the
Android Keystore, with a self-signed certificate. That certificate is its
TLS server certificate. Its SHA-256 over the DER encoding is the phone's
fingerprint.

**Connecting.** The computer opens TCP 8765 and sends the 8 bytes
`LUMATLS1`, then a TLS client handshake (TLS 1.2 or later) on the same
connection. It does not check the certificate against any authority; it
checks the fingerprint itself (below). Inside TLS everything is version 2:
`LUMAAVC2`, the hello, the statuses and the frame stream. Over Wi-Fi a
phone with `"tls": true` closes a connection that does not start with
`LUMATLS1`, and, unlike before, accepts only tokens it issued over TLS: a
computer allowed by a 0.6 phone is asked once more.

**Checking the phone.** The computer keeps the fingerprint of each phone
`id` it was allowed by. Before it sends the hello (and with it the token),
it compares the fingerprint of the TLS certificate: when it knows one for
that `id` and they differ, it closes the connection and tells the user
another device is posing as the phone. When it knows none, this is the
first time: it sends the hello with an empty token, and keeps the
fingerprint once the phone answers `"ok"`.

**The pairing code.** While the phone asks its user to allow the computer,
both show the same six-digit code, so the user can tell a device in
between apart (it would have to show its own certificate, which changes
the code). The code is the first four bytes of
SHA-256(certificate DER ‖ computer `id` in UTF-8), read as a big-endian
unsigned integer, modulo 1 000 000, written with six digits as "123 456".

A computer that does not know `"tls"` (Studio 0.6) cannot connect to such a
phone over Wi-Fi, only over USB; Studio 0.7 still connects to 0.6 phones,
which do not announce it, without encryption, and says so.

### On the computer

- Version 2 Studio still finds version 1 phones over USB (it sends
  `LUMALINK-DISCOVER-1` too and connects with `LUMAAVC1`), listed without a
  name. It no longer listens for version 1 phones over Wi-Fi: those show
  their in-app update first, and connect once updated.
- It lists every phone found, once per link ("Redmi Note 9 Pro · USB",
  "… · Wi-Fi"), and connects by itself to the phone the user chose last (its
  `id`), preferring USB when the same phone is on both. With no phone chosen
  yet it connects to the first one found on USB. When the connection drops
  it keeps looking and reconnects as soon as that phone is back.
- The zoom control spans the range of the selected lens from `lenses`; with a
  version 1 phone it stays at 1 … 10.

## Ports at a glance

| Port | Protocol | Listener | Purpose |
| --- | --- | --- | --- |
| 8765 | TCP | phone: USB in version 1; USB and Wi-Fi in version 2. Studio: Wi-Fi in version 1 only | Frame stream |
| 8766 | UDP | phone: USB tethering in version 1; Wi-Fi, tethering and loopback in version 2 | Discovery of phones |
| 8767 | UDP | Studio, version 1 only | Wi-Fi discovery of Studio; a version 2 phone also probes it, to tell its user an old Studio needs updating |

## Changing the protocol

Every desktop and phone app depends on this contract, and they are developed
separately. Keep changes backward compatible where possible, for example a new
optional JSON key that older phones ignore, and update this document in the
same pull request.
