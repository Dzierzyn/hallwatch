# HallWatch

Computer-vision system for corridor monitoring: motion detection, person detection
and **people counting** with tracking, audio detection, event recording with pre-roll,
cloud backup and a real-time web dashboard.

Built around one principle: **the privacy mask is the first step of the pipeline**, so areas
that do not belong to the system's owner never reach detection, never reach disk and never
reach the cloud.

*(Polish original of this document: [`README.pl.md`](README.pl.md))*

```text
┌────────┐   ┌──────────┐   ┌────────┐   ┌─────────────┐   ┌──────────┐
│ RTSP   │──▶│ PRIVACY  │──▶│ MOG2   │──▶│ YOLO11 +    │──▶│ line and │
│ camera │   │  MASK    │   │ motion │   │ ByteTrack   │   │ zone     │
└────────┘   │          │   │(~1 ms) │   │ (~16 ms MPS)│   │ counter  │
             └──────────┘   └────┬───┘   └─────────────┘   └────┬─────┘
                                 │ no motion → YOLO sleeps      │
                                 ▼                              ▼
        ┌────────────────────────────────────────────────────────────┐
        │  ring buffer (pre-roll) → H.264 clip → SQLite              │
        │  → S3 upload → ntfy push → MJPEG dashboard                 │
        └────────────────────────────────────────────────────────────┘
```

## Quick start

```bash
make install    # venv + dependencies + the .pth workaround described below

# 1. check everything works, without a camera, on synthetic video
make test

# 2. find the camera on the local network
hallwatch scan

# 3. inspect the stream: resolution, real FPS, presence of audio
hallwatch probe --source 'rtsp://user:password@192.168.1.50:554/h264Preview_01_main'

# 4. click out the counting line, zones and privacy masks
hallwatch zones --source 'rtsp://...'

# 5. run it
hallwatch run          # dashboard: http://127.0.0.1:8000
```

No camera yet? Everything runs on a Mac's built-in webcam (`source: "0"`) or on an `.mp4`
file, so the full pipeline can be developed offline.

### Known issue: macOS + uv + Python 3.12 or newer

`uv pip install -e .` succeeds and `import hallwatch` still raises `ModuleNotFoundError`.
The cause: uv sets the macOS `UF_HIDDEN` flag on `.pth` files, and since Python 3.12 `site.py`
**deliberately skips hidden `.pth` files**, so the editable-install path never reaches `sys.path`.
Diagnosis:

```bash
ls -lO .venv/lib/python3.12/site-packages/*.pth   # the flags column will show "hidden"
```

`make install` fixes it two ways: it clears the flag (`make fix-pth`) and additionally sets
`PYTHONPATH=src`, so it works even if the flag comes back.

## Multiple cameras, three operating modes

`config.yaml` describes `defaults` and a list of `cameras`; each camera overrides only what
actually differs. A single process serves all of them with a **shared database, upload queue
and notifications**, because events from different cameras have to sit on one timeline or the
analytics layer cannot join them into a single picture.

| Mode | For | Behaviour |
| --- | --- | --- |
| `continuous` | mains-powered camera | stream held open permanently |
| `on_demand` | battery camera | connects on a signal, then lets the camera sleep |
| `sampling` | traffic statistics | observes a window at fixed intervals and extrapolates |

The example in the repo counts people in a corridor in `on_demand` mode and vehicles on a
street (`classes: [2,3,5,7]`) in `sampling` mode with no recording. The dashboard has a camera
switch and every endpoint accepts `?camera=<slug>`.

**Why the street cannot run on motion detection.** A PIR-style trigger would miss most cars,
and there would be no way to know which ones, so the numbers would mean nothing. Sampling is
honest: we know exactly what fraction of the time we were watching, every event carries a
`1/duty_cycle` multiplier, and the analytics layer reconstructs the true rate. On demo data
recorded at 1/12 of the traffic, the extrapolation gives 19.6 events/h against a ground truth
of about 19.

A single-camera `config.yaml` from an older version still loads: it is wrapped into a
one-element list, so upgrading does not require rewriting the file.

## Battery cameras (`on_demand` mode)

Battery cameras **do not expose RTSP**. Neither Reolink nor Tapo do, and manufacturers block it
explicitly because of power draw. The exception is Reolink with a **Home Hub**, which exposes
RTSP on the camera's behalf with no subscription.

Physics remains, though: holding a stream open stops the camera from sleeping and drains the
battery in days. So `camera.mode: on_demand` does not hold a connection. It waits for a signal,
takes a short session and disconnects so the camera can go back to sleep:

```bash
curl -X POST http://127.0.0.1:8000/api/wake    # webhook: hub, Home Assistant, anything
make wake                                       # or from the terminal
```

A session ends after `session_idle_s` of silence or at the `session_seconds` hard limit.
`active_hours` (for example `"07:00-23:00"`) allows signals to be ignored at night.

**Live preview does not disappear in this mode.** It is available in every session, and the
dashboard's "preview on demand" button wakes the camera and holds the session open for as long
as you are watching, even when nothing moves in frame. The browser sends a `POST /api/watch`
heartbeat every 10 s; hiding the tab stops it, so the camera falls asleep by itself.
`watch_max_s` is the safety valve for a forgotten tab, since otherwise one open window would
flatten the battery. The MOG2 background model is rebuilt from scratch for each session with a
shortened warmup: after a break the old model is useless, and we know about the event from the
signal anyway.

## What it does

| Feature | Implementation |
| --- | --- |
| Motion detection | MOG2 background subtraction at half resolution, threshold on the fraction of frame area |
| Person detection | YOLO11 (Ultralytics), Apple MPS / CUDA / CPU acceleration |
| Tracking | ByteTrack, persistent `track_id` across frames |
| Crossing counts | sign change of the cross product against the segment, plus a check that the intersection lies within it |
| Zone presence | point-in-polygon test on the anchor (`feet` / `center`) |
| Audio detection | separate `ffmpeg -vn` process → PCM → dBFS with hysteresis |
| Recording | ring buffer pre-roll → pipe to `ffmpeg` → H.264 mp4 |
| Events | SQLite (WAL): events, crossings, per-minute aggregates |
| Cloud | any S3-compatible storage: Cloudflare R2, Backblaze B2, MinIO, AWS |
| Notifications | ntfy.sh with an event thumbnail, throttled |
| Dashboard | FastAPI + MJPEG, live counter, event timeline with a clip player |
| Retention | `hallwatch prune` removes media older than `retention_days` |

## Design decisions

**MOG2 as a gate in front of YOLO.** The corridor is empty most of the day. Running a neural
network 15 times a second for 24 hours wastes power with no information gain. MOG2 costs about
1 ms and decides when to wake the detector for `detection.awake_seconds`. In practice YOLO runs
a few percent of the time.

**The anchor depends on the mounting angle** (`counting.anchor`). A single point of the silhouette
decides whether the line was crossed, and which point is correct depends on where the camera is
looking from. For a side view or a downward angle along the corridor, the **feet** (`feet`) are
right: they stay on the floor, and it is the floor that crosses the line. The box centre would
jump when someone raises an arm or partially leaves frame. For a ceiling camera looking **straight
down** there are no feet: the silhouette is a blob whose lower edge shifts with every step, so the
only stable point is the **centre** (`center`). Both modes have a regression test asserting exactly
one crossing.

A top-down view has one more consequence: COCO was trained mostly on side views, so detection
confidence drops. The headroom is in `detection.conf` (0.25 to 0.30) and in the larger
`yolo11s.pt` model.

**Segment control, not an infinite line.** The sign of the cross product alone detects a crossing
of the *infinite line*, so someone walking well outside the line would still be counted. Projecting
the point onto the segment (parameter `t` in `[0,1]`) removes that error; the regression for it is
in `selftest` (step 4/4 requires exactly one crossing).

**Pre-roll via a ring buffer.** By the time the system decides an event has happened, the beginning
of it is already in the past. Every frame first lands in a `deque` of length `pre_roll_s`, and the
decision to record flushes the buffer to file. The clip shows someone walking in, not a fragment
starting halfway through.

**A reader thread that always holds the newest frame.** With RTSP, a slower consumer causes frames
to pile up in the buffer and latency to grow without bound. For live sources the thread overwrites
the newest frame and discards old ones; for files it reads sequentially so nothing is lost.

**`ffmpeg` through a pipe instead of `cv2.VideoWriter`.** This gives H.264 with `+faststart`,
playable in a browser without transcoding, and independence from whichever codecs happen to be
compiled into OpenCV.

## Configuration

Everything lives in [config.yaml](config.yaml). Line, zone and mask coordinates are **normalised
to 0..1**, so they survive a change of camera resolution.

Cloud credentials come from environment variables, not from the file:

```bash
export HALLWATCH_S3_KEY=...
export HALLWATCH_S3_SECRET=...
```

## Privacy and GDPR

The best safeguard is **framing**, not software: a camera aimed so that it sees only your own door
needs no masking at all. That is this system's default assumption. `privacy.masks` is empty, and
with an empty list `PrivacyMasker.apply()` returns the frame immediately, at zero cost.

When the frame cannot be constrained that way (mounting forced by the building, a wide-angle lens
catching someone else's entrance), there are tools:

- `privacy.masks`, areas blanked **before** detection, recording and upload; detections whose anchor
  falls inside a mask are discarded. `hallwatch zones` lets you draw them with the mouse
- `recording.retention_days` together with `hallwatch prune` for bounded retention
- no face recognition and no identification of individuals: the system counts anonymous objects of
  class `person`, and `track_id` lives only in process memory
- local storage by default, cloud optional and into a private bucket

A visible notice that the area is monitored is also worth having. It is cheap and it resolves most
misunderstandings before they start.

## Layout

```text
src/hallwatch/
  capture.py    RTSP / webcam / file reader with reconnect
  privacy.py    privacy masks (blur / black / pixelate)
  motion.py     MOG2 gate
  detect.py     YOLO11 + ByteTrack
  counter.py    line crossings, zone presence
  recorder.py   ring buffer + ffmpeg H.264
  store.py      SQLite: events, crossings, statistics
  audio.py      audio level from RTSP via ffmpeg
  cloud.py      background S3-compatible upload
  notify.py     ntfy push
  draw.py       overlay: boxes, tracks, HUD
  pipeline.py   state machine IDLE → AWAKE → EVENT
  web.py        FastAPI: dashboard, MJPEG, API
  tools.py      scan / probe / zone editor / selftest
```

## Analytics layer

Camera events feed a separate data pipeline in [`analytics/`](analytics/README.md): incremental
extract to Parquet, BigQuery through external tables over GCS, modelling in dbt (9 models,
23 tests), a 24-hour traffic forecast and anomaly detection, orchestrated with Airflow. The whole
stack also runs locally on DuckDB, with no cloud account.

## Next steps

- ReID across events (is this the same person who left an hour ago?)
- audio classification with YAMNet: knocking / door slam / shouting
- Docker plus `systemd` on a Raspberry Pi 5 or mini-PC, so the MacBook does not run 24/7
- Prometheus metrics export, alerts on night-time anomalies
