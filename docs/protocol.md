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
| `zoom` | number | `1` … `10`, clamped by the phone to what the lens supports |
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

## Ports at a glance

| Port | Protocol | Listener | Purpose |
| --- | --- | --- | --- |
| 8765 | TCP | phone (USB), Studio (Wi-Fi) | Frame stream |
| 8766 | UDP | phone | USB discovery |
| 8767 | UDP | Studio | Wi-Fi discovery |

## Changing the protocol

Every desktop and phone app depends on this contract, and they are developed
separately. Keep changes backward compatible where possible, for example a new
optional JSON key that older phones ignore, and update this document in the
same pull request.
