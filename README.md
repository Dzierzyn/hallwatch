# HallWatch

**Turn any camera into a private, smart monitor.** People and vehicle counting,
motion and audio events, recordings that start *before* something happens,
privacy masks, a live dashboard - all self-hosted, no accounts, no cloud
required, no subscriptions.

[![CI](https://github.com/Dzierzyn/hallwatch/actions/workflows/ci.yml/badge.svg)](https://github.com/Dzierzyn/hallwatch/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.10–3.13](https://img.shields.io/badge/python-3.10–3.13-blue.svg)](pyproject.toml)

Works with: laptop webcams · any RTSP camera (~$25 gets you one) · an old
phone as a camera (**$0** - see [docs/cameras.md](docs/cameras.md)) · video files.

*(Polska wersja: [README.pl.md](README.pl.md))*

```text
┌────────┐   ┌──────────┐   ┌────────┐   ┌─────────────┐   ┌──────────┐
│ camera │──▶│ PRIVACY  │──▶│ motion │──▶│ YOLO11 +    │──▶│ line and │
│  any   │   │  MASK    │   │ gate   │   │ ByteTrack   │   │ zone     │
└────────┘   │  first   │   │ ~1 ms  │   │             │   │ counter  │
             └──────────┘   └────┬───┘   └─────────────┘   └────┬─────┘
                                 │ no motion → YOLO sleeps      │
                                 ▼                              ▼
        ┌────────────────────────────────────────────────────────────┐
        │  pre-roll ring buffer → H.264 clip → SQLite → dashboard    │
        │  → optional: S3 backup · ntfy push · analytics (dbt + ML)  │
        └────────────────────────────────────────────────────────────┘
```

## Quick start

**Prerequisites:** Python 3.10-3.13 · [ffmpeg](https://ffmpeg.org) on PATH
(macOS: `brew install ffmpeg` · Debian/Ubuntu: `sudo apt install ffmpeg` ·
Windows: `winget install ffmpeg`) · optionally [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Dzierzyn/hallwatch && cd hallwatch
make install        # or: PYTHON=python3.12 make install-pip  (no uv needed)
make test           # 30 s, no camera required
make run            # → http://127.0.0.1:8000
```

That's it - the default config watches your **webcam** and counts people
crossing a line. Walk through the frame and watch the counter move.

First run downloads the YOLO11-nano model (~6 MB) automatically.

### Docker (best for RTSP cameras)

```bash
# edit config.yaml: set source: "rtsp://user:password@CAMERA-IP:554/stream1"
docker compose up   # → http://localhost:8000
```

No Python, no ffmpeg install - everything is in the image. Webcams are easier
with the native install (USB passthrough into containers is painful).

### Got a real camera?

```bash
make scan                                   # find RTSP cameras on your LAN
make probe SOURCE='rtsp://user:pass@ip:554/stream1'   # verify + measure FPS
make zones                                  # draw counting lines by clicking
```

Then put the URL into `config.yaml` under `source:` and `make run` again.
**No camera yet?** An old phone works great and costs nothing -
see [docs/cameras.md](docs/cameras.md) for that and for which cheap cameras
to buy (and which to avoid).

## What it does

| Feature | How |
| --- | --- |
| Motion detection | MOG2 background subtraction as a ~1 ms gate in front of the neural net |
| People / vehicle detection | YOLO11 (auto-selects Apple MPS / CUDA / CPU) |
| Tracking | ByteTrack - stable IDs across frames, so one person = one count |
| Line crossing counts | cross-product sign change + segment-bounds check, direction-aware |
| Zone presence | point-in-polygon on a configurable anchor (feet / centre) |
| Audio events | ffmpeg side-channel → dBFS with hysteresis |
| Event recording | pre-roll ring buffer → pipe to ffmpeg → browser-playable H.264 |
| Privacy masks | blur/black/pixelate applied **before** detection, disk and cloud |
| Storage | single-file SQLite: events, crossings, per-minute stats |
| Dashboard | FastAPI + MJPEG live view, event timeline with clip player, camera tabs |
| Notifications | [ntfy.sh](https://ntfy.sh) push with event snapshot - no account needed |
| Cloud backup | optional, any S3-compatible storage (R2 / B2 / MinIO / AWS) |
| Analytics | optional [dbt + ML stack](analytics/): traffic forecasting, anomaly detection |

## Three camera modes

| Mode | For | Behaviour |
| --- | --- | --- |
| `continuous` | mains-powered cameras, webcams | stream always open |
| `on_demand` | battery cameras | connects on a wake signal, then lets the camera sleep |
| `sampling` | traffic statistics | observes short windows on a schedule, extrapolates honestly |

Battery cameras deserve a warning: they **cannot stream 24/7** (hours, not
weeks, of battery) and most don't expose RTSP at all. Read
[docs/cameras.md](docs/cameras.md) *before* buying one.

Multiple cameras? One process runs them all with a shared database and a tab
switcher in the dashboard - see [examples/two-cameras.yaml](examples/two-cameras.yaml).

## Privacy by design

This tool watches spaces where people live. The design takes that seriously:

- **Masks come first in the pipeline**: a masked region never reaches
  detection, disk, dashboard or cloud. Not blurred-on-display - never captured.
- **No face recognition, no identity**: objects are anonymous class labels;
  track IDs live only in process memory.
- **Local by default**: recordings stay on your disk; cloud backup is opt-in
  to a bucket *you* own.
- **Bounded retention**: `make prune` deletes media older than
  `retention_days`, per camera.

If your camera can see a neighbour's door or window: point it away if you can
(the best privacy filter is the frame itself), mask what you can't avoid, put
up a visible notice, and check your local laws - in most of the EU, recording
shared/public space triggers GDPR obligations.

## Dashboard on other devices

The dashboard has **no authentication by default** and binds to `127.0.0.1`.
To reach it from your phone/LAN, set both of these in `config.yaml`:

```yaml
web:
  host: "0.0.0.0"
  auth_token: "pick-something-long-and-random"
```

Then open `http://<host-ip>:8000/?token=<your-token>` once per browser.
For remote access prefer an overlay network (Tailscale/WireGuard) or an
authenticated reverse proxy over port-forwarding.

## Design decisions (the interesting bits)

**A motion gate in front of YOLO.** Most scenes are empty most of the time.
Running a neural net 15×/s around the clock buys nothing; MOG2 costs ~1 ms
and wakes the detector only when pixels actually move.

**The anchor depends on camera angle** (`counting.anchor`). Feet stick to the
floor, and it's the floor that crosses your counting line - so `feet` is right
for corridor-style views. From straight above there are no feet, only a blob
whose bottom edge jitters every step - `center` is the stable choice there.

**Segment check, not line check.** A sign flip of the cross product detects
crossing an *infinite* line - someone walking far past its end would still
count. Projecting onto the segment (`t ∈ [0,1]`) kills that bug; a regression
test locks in exactly-one-crossing behaviour.

**Pre-roll via ring buffer.** When the system decides "this is an event", the
start of the event is already in the past. Every frame first lands in a
`pre_roll_s` ring buffer that gets flushed into the clip - so recordings show
someone *entering*, not a clip that starts halfway.

**Freshest-frame reader.** With RTSP, a consumer slower than the camera makes
frames queue up and the image drift seconds behind reality. For live sources
a reader thread keeps only the newest frame; files are read sequentially so
nothing is lost.

**Sampling is a property of the camera, not the hour.** Duty-cycled cameras
weight every event by 1/duty_cycle; hours with zero observed events still
carry the weight, otherwise extrapolated rates would be systematically
inflated (we hit exactly this bug - it's in the analytics README).

## Analytics (optional)

The [analytics/](analytics/) directory contains a full data stack over the
event database: incremental Parquet extraction, dbt models (tested), a
24-hour traffic forecast that beats a seasonal-naive baseline, and anomaly
detection. Runs entirely locally on DuckDB - or on BigQuery with the same
code. Orchestrated with Airflow. It's a separate opt-in; the camera pipeline
does not need it.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `requires a different Python: 3.14...` during install | Your `python3` is too new for PyTorch. `rm -rf .venv`, then `PYTHON=python3.12 make install-pip` (or use `make install` - uv downloads 3.12 itself) |
| `ModuleNotFoundError: hallwatch` after install on **macOS + uv** | `make fix-pth` (uv marks `.pth` files hidden; Python ≥3.12 skips hidden `.pth`). Linux/Windows: does not apply. |
| Webcam opens but frames are black (macOS) | System Settings → Privacy & Security → Camera → allow your terminal |
| `probe` works but `run` shows nothing (RTSP) | Usually Wi-Fi + UDP; HallWatch forces RTSP-over-TCP unless you set `OPENCV_FFMPEG_CAPTURE_OPTIONS` yourself - check camera sub-stream and credentials |
| Recording disabled | Install ffmpeg and restart |
| Slow on Raspberry Pi / old laptop | Lower `width`, `fps_limit`, `imgsz` - see [docs/cameras.md](docs/cameras.md#low-power-hardware-raspberry-pi-old-laptops) |
| Windows | Native install works (`make install-pip` or plain `pip install -e .`); use `py -3.12`. WSL2 works for RTSP sources. |

## Contributing

The most valuable contribution is a **camera compatibility report** - there's
an issue template for it, no code required. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the dev setup (TL;DR: `make install`,
`make test`, ruff).

Security reports: see [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE) - do what you like, no warranty.
