# Getting a camera

HallWatch works with anything OpenCV/ffmpeg can read. The one thing that
matters when buying: **the camera must expose an RTSP stream**. Many popular
consumer cameras (Ring, Nest, Eufy, most Xiaomi/Aqara) lock video inside their
own cloud apps - avoid them for this project.

## Free: hardware you already own

| Source | How | Cost |
| --- | --- | --- |
| Laptop webcam | `source: "0"` | 0 |
| Old Android phone | Install **IP Webcam** (free), start server, use `source: "http://PHONE-IP:8080/video"` | 0 |
| Old iPhone/Android | Any "RTSP camera server" app → `source: "rtsp://PHONE-IP:8554/..."` | 0 |
| A video file | `source: "path/to/clip.mp4"` - great for testing detection settings | 0 |

The old-phone route is genuinely good: a phone on a charger, wedged in a
corner, is a 1080p camera with better low-light performance than most cheap
IP cams. Start here before buying anything.

## Cheap wired cameras that just work (~$20-40)

These expose RTSP after a one-time toggle in their app:

| Camera | RTSP | Notes |
| --- | --- | --- |
| TP-Link Tapo C1xx/C2xx series | `rtsp://user:pass@IP:554/stream1` (`stream2` = low-res) | Create a separate "camera account" in the Tapo app first |
| Reolink wired (E1, PoE models) | `rtsp://user:pass@IP:554/h264Preview_01_main` (`_sub` = low-res) | RTSP on by default; also ONVIF |
| Generic "ONVIF" cameras (many brands) | varies - try `/onvif1`, `/live/ch00_0`, `/stream1` | `hallwatch scan` finds them; `hallwatch probe` tests URLs |

**Tips that save frustration:**

- **Use the sub-stream** (`stream2`, `_sub`) as your `source`. Detection runs
  at 640 px anyway - pulling the 4K main stream wastes bandwidth and CPU.
- **2.4 GHz Wi-Fi**: most cheap cameras cannot join 5 GHz-only networks.
- Special characters in the RTSP password must be URL-encoded (`@` → `%40`).
- Verify before mounting anything: `hallwatch probe --source 'rtsp://...'`

## Battery cameras: read this before buying

Battery cameras **do not expose RTSP** - all major vendors (Reolink, TP-Link)
disable it because keeping a stream open prevents sleep and drains the battery
in hours, not weeks.

Two workable paths:

1. **Reolink battery camera + Reolink Home Hub.** The hub is mains-powered and
   exposes RTSP *on the camera's behalf* (enable in app: Settings → Network →
   Advanced → RTSP/ONVIF). Pair it with HallWatch's `on_demand` mode, which
   connects only on a wake signal and lets the camera sleep - see
   [`examples/battery-camera.yaml`](../examples/battery-camera.yaml).
2. **Skip the battery.** A wired camera near a socket, or a flat USB cable
   run under a door frame, avoids the whole problem and costs less.

Rough battery budget with `on_demand` (21.6 Wh class camera): motion events
only ≈ 7-9 weeks per charge; plus 5 min of live viewing per day ≈ 4-6 weeks;
a permanently open stream ≈ 8-13 **hours**. Cold halves these numbers.

## Which mode for which camera

| Situation | `mode` | Why |
| --- | --- | --- |
| Mains-powered camera or webcam | `continuous` | no reason not to watch everything |
| Battery camera | `on_demand` | the stream must not stay open |
| Traffic/footfall statistics | `sampling` | honest rates from a duty-cycled sample; motion triggers would silently miss most objects |

## Low-power hardware (Raspberry Pi, old laptops)

CPU-only detection works; tune it:

```yaml
defaults:
  width: 640
  fps_limit: 5
  detection:
    imgsz: 416 # or 320; halves compute again
    model: "yolo11n.pt"
  recording:
    codec: "h264_v4l2m2m" # Raspberry Pi hardware encoder (default: libx264)
```

The MOG2 motion gate matters most here: YOLO runs only when something moves,
so an idle scene costs ~1 ms/frame regardless of hardware.
