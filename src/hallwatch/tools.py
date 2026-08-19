"""Helper tools: network scanning, stream testing, the zone editor, selftest."""

from __future__ import annotations

import concurrent.futures
import logging
import socket
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np
import yaml

from .capture import FrameSource
from .config import Config

log = logging.getLogger(__name__)

# ports typical for IP cameras
CAMERA_PORTS = {
    554: "RTSP",
    8554: "RTSP (alt)",
    80: "HTTP / panel www",
    8000: "ONVIF / Hikvision",
    2020: "ONVIF (Reolink)",
    37777: "Dahua",
}

# most common RTSP paths by vendor
RTSP_PATHS = {
    "Reolink": ["/h264Preview_01_main", "/h264Preview_01_sub"],
    "TP-Link Tapo": ["/stream1", "/stream2"],
    "Hikvision": ["/Streaming/Channels/101", "/Streaming/Channels/102"],
    "Dahua / Imou": [
        "/cam/realmonitor?channel=1&subtype=0",
        "/cam/realmonitor?channel=1&subtype=1",
    ],
    "ONVIF (generic)": ["/onvif1", "/live/ch00_0", "/videoMain"],
}


def local_subnet() -> str | None:
    """Returns the /24 prefix of the active interface, e.g. '192.168.1'."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        return ip.rsplit(".", 1)[0]
    except OSError:
        return None
    finally:
        s.close()


def _probe_port(host: str, port: int, timeout: float = 0.4) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def scan_network(subnet: str | None = None) -> list[tuple[str, list[str]]]:
    """Searches the LAN for hosts with open camera ports."""
    subnet = subnet or local_subnet()
    if not subnet:
        print("Cannot determine the subnet - pass it explicitly: hallwatch scan --subnet 192.168.1")
        return []

    print(f"Scanning {subnet}.1-254 on ports {', '.join(map(str, CAMERA_PORTS))} ...\n")
    hosts = [f"{subnet}.{i}" for i in range(1, 255)]
    found: list[tuple[str, list[str]]] = []

    def check(host: str) -> tuple[str, list[str]]:
        open_ports = [f"{p} ({CAMERA_PORTS[p]})" for p in CAMERA_PORTS if _probe_port(host, p)]
        return host, open_ports

    with concurrent.futures.ThreadPoolExecutor(max_workers=128) as pool:
        for host, ports in pool.map(check, hosts):
            if ports:
                found.append((host, ports))
                marker = "  <-- camera candidate" if any("RTSP" in p for p in ports) else ""
                print(f"  {host:<16} {', '.join(ports)}{marker}")

    if not found:
        print("  nothing found (the camera may be on another subnet or on guest Wi-Fi)")
    else:
        print("\nOnce you know the camera IP, check the RTSP paths:")
        for vendor, paths in RTSP_PATHS.items():
            print(f"  {vendor:<18} rtsp://user:password@IP:554{paths[0]}")
        print("\nThen: hallwatch probe --source 'rtsp://user:password@IP:554/...'")
    return found


def ffprobe_info(source: str) -> str:
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-hide_banner",
                "-rtsp_transport",
                "tcp",
                "-show_streams",
                "-show_format",
                "-of",
                "default=noprint_wrappers=1",
                source,
            ],
            capture_output=True,
            text=True,
            timeout=25,
        )
        return out.stdout or out.stderr
    except Exception as exc:  # noqa: BLE001
        return f"ffprobe failed: {exc}"


def probe_source(source: str, seconds: float = 6.0, preview: bool = True) -> bool:
    """Checks that the source works: prints its parameters and measures real FPS."""
    print(f"== Source: {source}\n")

    if str(source).lower().startswith("rtsp://"):
        info = ffprobe_info(source)
        keys = (
            "codec_name",
            "codec_type",
            "width",
            "height",
            "r_frame_rate",
            "bit_rate",
            "sample_rate",
            "channels",
        )
        lines = [ln for ln in info.splitlines() if ln.split("=")[0] in keys]
        if lines:
            print("Streams according to ffprobe:")
            for ln in lines:
                print("  " + ln)
            has_audio = any("codec_type=audio" in ln for ln in info.splitlines())
            print(
                f"\n  audio in the stream: {'YES' if has_audio else 'NO'}"
                f"{'' if has_audio else '  (audio detection will be inactive)'}"
            )
        else:
            print("ffprobe returned no stream information:\n" + info[:800])
        print()

    try:
        src = FrameSource(source, width=None).start()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: could not open the source: {exc}")
        return False

    frames, t0, shape = 0, time.time(), None
    try:
        while time.time() - t0 < seconds:
            frame, _ = src.read(timeout=6.0)
            if frame is None:
                if src.finished:
                    break
                continue
            frames += 1
            shape = frame.shape
            if preview:
                cv2.imshow("probe (q = quit)", frame)
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break
    finally:
        src.stop()
        if preview:
            cv2.destroyAllWindows()

    elapsed = time.time() - t0
    if not frames or shape is None:
        print("ERROR: zero frames. Check the login/password and the RTSP path.")
        return False
    if src.live:
        print(
            f"OK: {frames} frames in {elapsed:.1f}s -> {frames / elapsed:.1f} FPS measured, "
            f"resolution {shape[1]}x{shape[0]}"
        )
        if src.native_fps:
            print(f"    FPS declared by the source: {src.native_fps:.1f}")
        if src.native_fps and frames / elapsed < src.native_fps * 0.6:
            print(
                "    WARNING: real FPS well below the declared one, weak Wi-Fi "
                "or too large a stream. Consider the camera substream."
            )
    else:
        # a file is read as fast as the disk allows, so an FPS measurement means nothing here
        print(
            f"OK: file read, {frames} frames, resolution {shape[1]}x{shape[0]}, "
            f"file FPS {src.native_fps:.1f}"
        )
    return True


# ---------------------------------------------------------------------------
# Zone editor
# ---------------------------------------------------------------------------
HELP = """
  LEFT-click to add points.
  1 = counting line (2 points)       2 = presence zone       3 = privacy mask
  ENTER = confirm shape              u = undo point          c = clear all
  s = save to zones.generated.yaml   q / ESC = quit
"""


class ZoneEditor:
    MODES = {ord("1"): "line", ord("2"): "zone", ord("3"): "mask"}
    COLORS = {"line": (60, 220, 250), "zone": (80, 220, 120), "mask": (70, 70, 235)}

    def __init__(self, frame: np.ndarray) -> None:
        self.frame = frame
        self.h, self.w = frame.shape[:2]
        self.mode = "line"
        self.points: list[tuple[int, int]] = []
        self.line: list[tuple[float, float]] | None = None
        self.zones: list[dict] = []
        self.masks: list[dict] = []

    def _norm(self, pts: list[tuple[int, int]]) -> list[list[float]]:
        return [[round(x / self.w, 4), round(y / self.h, 4)] for x, y in pts]

    def on_mouse(self, event: int, x: int, y: int, flags: int, param: object) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            self.points.append((x, y))
            if self.mode == "line" and len(self.points) == 2:
                self.commit()

    def commit(self) -> None:
        need = 2 if self.mode == "line" else 3
        if len(self.points) < need:
            print(f"  need at least {need} points for '{self.mode}'")
            return
        if self.mode == "line":
            self.line = self._norm(self.points)  # type: ignore[assignment]
            print(f"  line: {self.line}")
        elif self.mode == "zone":
            name = f"zone-{len(self.zones) + 1}"
            self.zones.append({"name": name, "polygon": self._norm(self.points)})
            print(f"  zone '{name}' ({len(self.points)} pts)")
        else:
            name = f"mask-{len(self.masks) + 1}"
            self.masks.append(
                {"name": name, "polygon": self._norm(self.points), "mode": "blur", "strength": 35}
            )
            print(f"  mask '{name}' ({len(self.points)} pts)")
        self.points = []

    def render(self) -> np.ndarray:
        canvas = self.frame.copy()
        for m in self.masks:
            pts = np.array([[p[0] * self.w, p[1] * self.h] for p in m["polygon"]], np.int32)
            overlay = canvas.copy()
            cv2.fillPoly(overlay, [pts], self.COLORS["mask"])
            cv2.addWeighted(overlay, 0.35, canvas, 0.65, 0, dst=canvas)
            cv2.polylines(canvas, [pts], True, self.COLORS["mask"], 2)
        for z in self.zones:
            pts = np.array([[p[0] * self.w, p[1] * self.h] for p in z["polygon"]], np.int32)
            cv2.polylines(canvas, [pts], True, self.COLORS["zone"], 2)
            cv2.putText(
                canvas,
                z["name"],
                tuple(pts[0]),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                self.COLORS["zone"],
                1,
                cv2.LINE_AA,
            )
        if self.line:
            pts = [(int(p[0] * self.w), int(p[1] * self.h)) for p in self.line]
            cv2.line(canvas, pts[0], pts[1], self.COLORS["line"], 2, cv2.LINE_AA)
        for p in self.points:
            cv2.circle(canvas, p, 4, self.COLORS[self.mode], -1, cv2.LINE_AA)
        if len(self.points) > 1:
            cv2.polylines(
                canvas,
                [np.array(self.points, np.int32)],
                False,
                self.COLORS[self.mode],
                1,
                cv2.LINE_AA,
            )
        cv2.rectangle(canvas, (0, 0), (self.w, 28), (20, 20, 20), -1)
        cv2.putText(
            canvas,
            f"mode: {self.mode}  points: {len(self.points)}  "
            f"[1/2/3 mode, ENTER ok, u undo, s save, q quit]",
            (10, 19),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.46,
            (230, 230, 230),
            1,
            cv2.LINE_AA,
        )
        return canvas

    def as_yaml(self) -> str:
        data = {
            "counting": {
                "line": self.line,
                "direction_labels": {"positive": "in", "negative": "out"},
                "zones": self.zones,
            },
            "privacy": {"masks": self.masks},
        }
        return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=None)


def edit_zones(cfg: Config, source: str | None = None, camera: str | None = None) -> None:
    prof = cfg.camera(camera)
    source = source or prof.source
    print(f"Camera '{prof.name}', source: {source}")
    src = FrameSource(source, width=prof.width).start()
    frame = None
    for _ in range(60):  # a few frames to let auto-exposure warm up
        frame, _ = src.read(timeout=6.0)
        if frame is not None:
            break
    src.stop()
    if frame is None:
        print("Could not grab a frame.")
        return

    print(HELP)
    editor = ZoneEditor(frame)
    win = "HallWatch - zone editor"
    cv2.namedWindow(win)
    cv2.setMouseCallback(win, editor.on_mouse)

    while True:
        cv2.imshow(win, editor.render())
        key = cv2.waitKey(20) & 0xFF
        if key in (ord("q"), 27):
            break
        if key in ZoneEditor.MODES:
            editor.mode = ZoneEditor.MODES[key]
            editor.points = []
            print(f"  mode: {editor.mode}")
        elif key in (13, 10):
            editor.commit()
        elif key == ord("u") and editor.points:
            editor.points.pop()
        elif key == ord("c"):
            editor.points, editor.line, editor.zones, editor.masks = [], None, [], []
            print("  cleared")
        elif key == ord("s"):
            out = cfg.root / f"zones.{prof.slug}.yaml"
            out.write_text(editor.as_yaml(), encoding="utf-8")
            print(
                f"\n  saved {out}\n  Paste 'counting' and 'privacy' under the camera "
                f"entry '{prof.name}' in config.yaml:\n"
            )
            print(editor.as_yaml())
    cv2.destroyAllWindows()


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------
def make_test_video(path: Path, seconds: float = 10.0, fps: int = 12) -> Path:
    """Synthetic video: grey background + noise + a rectangle moving across the frame."""
    from .recorder import ClipWriter

    w, h = 640, 360
    writer = ClipWriter(path, w, h, fps, crf=24)
    total = int(seconds * fps)
    rng = np.random.default_rng(7)
    for i in range(total):
        frame = np.full((h, w, 3), 90, np.uint8)
        frame += rng.integers(0, 6, (h, w, 3), dtype=np.uint8)  # sensor noise
        if i > fps * 2:  # the object enters after 2 s
            x = int((i - fps * 2) * 8) % (w + 120) - 60
            cv2.rectangle(frame, (x, 150), (x + 55, 300), (225, 225, 225), -1)
        writer.feed(frame, i / fps)
    result = writer.close()
    return result or path


def make_person_video(path: Path, detector: object, seconds: float = 8.0, fps: int = 12) -> Path:
    """Video with a REAL figure (cut out of a reference image) walking down the frame.

    Used as a regression for the counting logic: the figure must cross the default
    line (y = 0.62) exactly once, so the correct result is count_in == 1.
    """
    from ultralytics.utils import ASSETS

    from .recorder import ClipWriter

    src = cv2.imread(str(ASSETS / "bus.jpg"))
    found = detector.detect(src, track=False)  # type: ignore[attr-defined]
    if not found:
        raise RuntimeError("no people in the reference image, cannot build the test video")
    biggest = max(found, key=lambda d: (d.xyxy[3] - d.xyxy[1]) * (d.xyxy[2] - d.xyxy[0]))
    x1, y1, x2, y2 = (int(v) for v in biggest.xyxy)
    crop = src[max(0, y1) : y2, max(0, x1) : x2]
    ph = 150
    pw = max(1, int(crop.shape[1] * ph / crop.shape[0]))
    crop = cv2.resize(crop, (pw, ph), interpolation=cv2.INTER_AREA)

    w, h = 640, 360
    writer = ClipWriter(path, w, h, fps, crf=23)
    total = int(seconds * fps)
    rng = np.random.default_rng(11)
    # the feet (bottom edge of the cutout) must cross the line y = 0.62*360 = 223
    y_from, y_to = 10, h - ph - 8
    x = w // 2 - pw // 2
    for i in range(total):
        frame = np.full((h, w, 3), 110, np.uint8)
        frame += rng.integers(0, 5, (h, w, 3), dtype=np.uint8)
        cv2.rectangle(frame, (0, 300), (w, h), (85, 85, 85), -1)  # a "floor" for context
        if i > fps:  # 1 s of calm so that MOG2 can learn the background
            t = min(1.0, (i - fps) / max(1, total - fps - 1))
            y = int(y_from + (y_to - y_from) * t)
            frame[y : y + ph, x : x + pw] = crop
        writer.feed(frame, i / fps)
    return writer.close() or path


def selftest(cfg: Config) -> bool:
    """Checks the detector, recording, the whole pipeline and the counting logic."""
    from .pipeline import Pipeline
    from .recorder import ffmpeg_available

    ok = True

    print("== 1/4 YOLO detector on the reference image")
    try:
        from ultralytics.utils import ASSETS

        from .detect import PersonDetector

        det = PersonDetector(cfg.cameras[0].detection)
        img = cv2.imread(str(ASSETS / "bus.jpg"))
        found = det.detect(img, track=False)
        print(f"   device: {det.device}, people detected: {len(found)}")
        if len(found) < 3:
            print("   WARNING: expected >=3 people in bus.jpg")
            ok = False
        else:
            print("   OK")
    except Exception as exc:  # noqa: BLE001
        print(f"   ERROR: {exc}")
        return False

    if not ffmpeg_available():
        print(
            "\nffmpeg not found - install it (macOS: brew install ffmpeg | "
            "Debian/Ubuntu: sudo apt install ffmpeg | Windows: winget install ffmpeg)"
        )
        return False

    print("\n== 2/4 Recording (ffmpeg -> H.264)")
    tmp = cfg.path("data/selftest")
    tmp.mkdir(parents=True, exist_ok=True)
    video = make_test_video(tmp / "synthetic.mp4")
    if video.exists() and video.stat().st_size > 1000:
        print(f"   OK: {video.name}, {video.stat().st_size / 1024:.0f} kB")
    else:
        print("   ERROR: could not write the test video")
        return False

    def run_on(video_path: Path, tag: str) -> tuple:
        test_cfg = cfg.model_copy(deep=True)
        test_cfg.root = cfg.root
        cam = test_cfg.cameras[0]
        cam.source = str(video_path)
        cam.fps_limit = None
        cam.mode = "continuous"
        cam.audio.enabled = False
        cam.privacy.masks = []
        cam.recording.dir = f"data/selftest/clips-{tag}"
        cam.recording.post_roll_s = 1.0
        # The counting regression must not depend on the user's config: force
        # the line the synthetic person video is built to cross exactly once.
        cam.counting.line = ((0.08, 0.62), (0.92, 0.62))
        cam.counting.anchor = "feet"
        test_cfg.cameras = [cam]
        test_cfg.notify.enabled = False
        test_cfg.cloud.enabled = False
        test_cfg.storage.db = f"data/selftest/{tag}.db"

        pipe = Pipeline(test_cfg, test_cfg.cameras[0])
        pipe.run()
        result = (
            pipe.snapshot_state(),
            pipe.store.recent_events(limit=10),
            list(test_cfg.path(pipe.prof.clip_dir).glob("*.mp4")),
            pipe.counter.state,
        )
        pipe.stop()
        return result

    print("\n== 3/4 Full pipeline on video with motion")
    state, events, clips, _ = run_on(video, "motion")
    print(f"   frames processed:   {state.frames_total}")
    print(f"   events in database: {len(events)}")
    print(f"   clips written:      {len(clips)}")
    if state.frames_total < 50:
        print("   ERROR: the pipeline processed too few frames")
        ok = False
    elif not events:
        print("   ERROR: motion detection did not create an event")
        ok = False
    elif not clips:
        print("   ERROR: no clip was recorded")
        ok = False
    else:
        print(f"   OK: event #{events[0].id} ({events[0].kind}), clip {events[0].clip_path}")

    print("\n== 4/4 Person detection, tracking and crossing counts")
    try:
        person_video = make_person_video(tmp / "person.mp4", det)
        state, events, clips, counts = run_on(person_video, "person")
        person_events = [e for e in events if e.kind == "person"]
        total_crossings = counts.positive + counts.negative
        print(f"   unique tracks:      {counts.unique_seen}")
        print(f"   line crossings:     {counts.positive} down / {counts.negative} up")
        print(f"   'person' events:    {len(person_events)}")
        if not person_events:
            print("   ERROR: the person was not recognised as a 'person' event")
            ok = False
        elif counts.unique_seen == 0:
            print("   ERROR: the tracker assigned no ID")
            ok = False
        elif total_crossings != 1:
            print(f"   ERROR: expected exactly 1 crossing, got {total_crossings}")
            ok = False
        else:
            ev = person_events[0]
            print(
                f"   OK: event #{ev.id}, max people {ev.max_persons}, "
                f"in={ev.count_in} out={ev.count_out}, thumbnail {ev.snapshot_path}"
            )
    except Exception as exc:  # noqa: BLE001
        print(f"   ERROR: {exc}")
        ok = False

    print("\n" + ("ALL CHECKS PASSED" if ok else "PROBLEMS FOUND - see above"))
    return ok
