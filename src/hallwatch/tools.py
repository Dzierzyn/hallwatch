"""Narzedzia pomocnicze: skanowanie sieci, test strumienia, edytor strefowy, selftest."""

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

from .capture import FrameSource, parse_source
from .config import Config

log = logging.getLogger(__name__)

# porty typowe dla kamer IP
CAMERA_PORTS = {
    554: "RTSP",
    8554: "RTSP (alt)",
    80: "HTTP / panel www",
    8000: "ONVIF / Hikvision",
    2020: "ONVIF (Reolink)",
    37777: "Dahua",
}

# najczestsze sciezki RTSP wg producenta
RTSP_PATHS = {
    "Reolink": ["/h264Preview_01_main", "/h264Preview_01_sub"],
    "TP-Link Tapo": ["/stream1", "/stream2"],
    "Hikvision": ["/Streaming/Channels/101", "/Streaming/Channels/102"],
    "Dahua / Imou": ["/cam/realmonitor?channel=1&subtype=0", "/cam/realmonitor?channel=1&subtype=1"],
    "ONVIF (generic)": ["/onvif1", "/live/ch00_0", "/videoMain"],
}


def local_subnet() -> str | None:
    """Zwraca prefiks /24 aktywnego interfejsu, np. '192.168.1'."""
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
    """Szuka w LAN hostow z otwartymi portami kamerowymi."""
    subnet = subnet or local_subnet()
    if not subnet:
        print("Nie moge ustalic podsieci - podaj ja: hallwatch scan --subnet 192.168.1")
        return []

    print(f"Skanuje {subnet}.1-254 na portach {', '.join(map(str, CAMERA_PORTS))} ...\n")
    hosts = [f"{subnet}.{i}" for i in range(1, 255)]
    found: list[tuple[str, list[str]]] = []

    def check(host: str) -> tuple[str, list[str]]:
        open_ports = [f"{p} ({CAMERA_PORTS[p]})" for p in CAMERA_PORTS if _probe_port(host, p)]
        return host, open_ports

    with concurrent.futures.ThreadPoolExecutor(max_workers=128) as pool:
        for host, ports in pool.map(check, hosts):
            if ports:
                found.append((host, ports))
                marker = "  <-- kandydat na kamere" if any("RTSP" in p for p in ports) else ""
                print(f"  {host:<16} {', '.join(ports)}{marker}")

    if not found:
        print("  nic nie znalazlem (kamera moze byc w innej podsieci / na Wi-Fi guest)")
    else:
        print("\nGdy znasz IP kamery, sprawdz sciezki RTSP:")
        for vendor, paths in RTSP_PATHS.items():
            print(f"  {vendor:<18} rtsp://user:haslo@IP:554{paths[0]}")
        print("\nPotem: hallwatch probe --source 'rtsp://user:haslo@IP:554/...'")
    return found


def ffprobe_info(source: str) -> str:
    try:
        out = subprocess.run(
            ["ffprobe", "-hide_banner", "-rtsp_transport", "tcp", "-show_streams",
             "-show_format", "-of", "default=noprint_wrappers=1", source],
            capture_output=True, text=True, timeout=25,
        )
        return out.stdout or out.stderr
    except Exception as exc:  # noqa: BLE001
        return f"ffprobe nieudany: {exc}"


def probe_source(source: str, seconds: float = 6.0, preview: bool = True) -> bool:
    """Sprawdza, czy zrodlo dziala: wypisuje parametry i mierzy realny FPS."""
    print(f"== Zrodlo: {source}\n")

    if str(source).lower().startswith("rtsp://"):
        info = ffprobe_info(source)
        keys = ("codec_name", "codec_type", "width", "height", "r_frame_rate", "bit_rate",
                "sample_rate", "channels")
        lines = [ln for ln in info.splitlines() if ln.split("=")[0] in keys]
        if lines:
            print("Strumienie wg ffprobe:")
            for ln in lines:
                print("  " + ln)
            has_audio = any("codec_type=audio" in ln for ln in info.splitlines())
            print(f"\n  audio w strumieniu: {'TAK' if has_audio else 'NIE'}"
                  f"{'' if has_audio else '  (detekcja dzwieku bedzie nieaktywna)'}")
        else:
            print("ffprobe nie zwrocil informacji o strumieniach:\n" + info[:800])
        print()

    try:
        src = FrameSource(source, width=None).start()
    except Exception as exc:  # noqa: BLE001
        print(f"BLAD: nie udalo sie otworzyc zrodla: {exc}")
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
                cv2.imshow("probe (q = wyjscie)", frame)
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break
    finally:
        src.stop()
        if preview:
            cv2.destroyAllWindows()

    elapsed = time.time() - t0
    if not frames or shape is None:
        print("BLAD: zero klatek. Sprawdz login/haslo i sciezke RTSP.")
        return False
    if src.live:
        print(f"OK: {frames} klatek w {elapsed:.1f}s -> {frames/elapsed:.1f} FPS realnie, "
              f"rozdzielczosc {shape[1]}x{shape[0]}")
        if src.native_fps:
            print(f"    FPS deklarowany przez zrodlo: {src.native_fps:.1f}")
        if src.native_fps and frames / elapsed < src.native_fps * 0.6:
            print("    UWAGA: realny FPS mocno ponizej deklarowanego - slabe Wi-Fi "
                  "albo za duzy strumien. Rozwaz substream kamery.")
    else:
        # plik czytamy tak szybko, jak zdazy dysk - pomiar FPS nic tu nie znaczy
        print(f"OK: plik odczytany, {frames} klatek, rozdzielczosc {shape[1]}x{shape[0]}, "
              f"FPS pliku {src.native_fps:.1f}")
    return True


# ---------------------------------------------------------------------------
# Edytor stref
# ---------------------------------------------------------------------------
HELP = """
  Klikaj LEWYM przyciskiem, by dodawac punkty.
  1 = linia zliczajaca (2 punkty)    2 = strefa obecnosci    3 = maska prywatnosci
  ENTER = zatwierdz ksztalt          u = cofnij punkt        c = wyczysc wszystko
  s = zapisz do zones.generated.yaml q / ESC = wyjscie
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
            print(f"  potrzebne min. {need} punkty dla '{self.mode}'")
            return
        if self.mode == "line":
            self.line = self._norm(self.points)  # type: ignore[assignment]
            print(f"  linia: {self.line}")
        elif self.mode == "zone":
            name = f"strefa-{len(self.zones)+1}"
            self.zones.append({"name": name, "polygon": self._norm(self.points)})
            print(f"  strefa '{name}' ({len(self.points)} pkt)")
        else:
            name = f"maska-{len(self.masks)+1}"
            self.masks.append(
                {"name": name, "polygon": self._norm(self.points), "mode": "blur", "strength": 35}
            )
            print(f"  maska '{name}' ({len(self.points)} pkt)")
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
            cv2.putText(canvas, z["name"], tuple(pts[0]), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        self.COLORS["zone"], 1, cv2.LINE_AA)
        if self.line:
            pts = [(int(p[0] * self.w), int(p[1] * self.h)) for p in self.line]
            cv2.line(canvas, pts[0], pts[1], self.COLORS["line"], 2, cv2.LINE_AA)
        for p in self.points:
            cv2.circle(canvas, p, 4, self.COLORS[self.mode], -1, cv2.LINE_AA)
        if len(self.points) > 1:
            cv2.polylines(canvas, [np.array(self.points, np.int32)], False,
                          self.COLORS[self.mode], 1, cv2.LINE_AA)
        cv2.rectangle(canvas, (0, 0), (self.w, 28), (20, 20, 20), -1)
        cv2.putText(canvas, f"tryb: {self.mode}  punkty: {len(self.points)}  "
                            f"[1/2/3 tryb, ENTER ok, u cofnij, s zapisz, q wyjscie]",
                    (10, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.46, (230, 230, 230), 1, cv2.LINE_AA)
        return canvas

    def as_yaml(self) -> str:
        data = {
            "counting": {
                "line": self.line,
                "direction_labels": {"positive": "wejscie", "negative": "wyjscie"},
                "zones": self.zones,
            },
            "privacy": {"masks": self.masks},
        }
        return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=None)


def edit_zones(cfg: Config, source: str | None = None, camera: str | None = None) -> None:
    prof = cfg.camera(camera)
    source = source or prof.source
    print(f"Kamera '{prof.name}', zrodlo: {source}")
    src = FrameSource(source, width=prof.width).start()
    frame = None
    for _ in range(60):  # kilka klatek na rozgrzanie autoexpozycji
        frame, _ = src.read(timeout=6.0)
        if frame is not None:
            break
    src.stop()
    if frame is None:
        print("Nie udalo sie pobrac klatki.")
        return

    print(HELP)
    editor = ZoneEditor(frame)
    win = "HallWatch - edytor stref"
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
            print(f"  tryb: {editor.mode}")
        elif key in (13, 10):
            editor.commit()
        elif key == ord("u") and editor.points:
            editor.points.pop()
        elif key == ord("c"):
            editor.points, editor.line, editor.zones, editor.masks = [], None, [], []
            print("  wyczyszczono")
        elif key == ord("s"):
            out = cfg.root / f"zones.{prof.slug}.yaml"
            out.write_text(editor.as_yaml(), encoding="utf-8")
            print(f"\n  zapisano {out}\n  Wklej 'counting' i 'privacy' pod wpis kamery '{prof.name}' w config.yaml:\n")
            print(editor.as_yaml())
    cv2.destroyAllWindows()


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------
def make_test_video(path: Path, seconds: float = 10.0, fps: int = 12) -> Path:
    """Syntetyczne wideo: szare tlo + szum + prostokat przejezdzajacy przez kadr."""
    from .recorder import ClipWriter

    w, h = 640, 360
    writer = ClipWriter(path, w, h, fps, crf=24)
    total = int(seconds * fps)
    rng = np.random.default_rng(7)
    for i in range(total):
        frame = np.full((h, w, 3), 90, np.uint8)
        frame += rng.integers(0, 6, (h, w, 3), dtype=np.uint8)  # szum czujnika
        if i > fps * 2:  # obiekt wjezdza po 2 s
            x = int((i - fps * 2) * 8) % (w + 120) - 60
            cv2.rectangle(frame, (x, 150), (x + 55, 300), (225, 225, 225), -1)
        writer.feed(frame, i / fps)
    result = writer.close()
    return result or path


def make_person_video(path: Path, detector: object, seconds: float = 8.0, fps: int = 12) -> Path:
    """Wideo z PRAWDZIWA postacia (wyciecie z obrazka wzorcowego) idaca w dol kadru.

    Sluzy do regresji logiki liczenia: postac musi przekroczyc domyslna linie
    (y = 0.62) dokladnie raz, wiec poprawny wynik to count_in == 1.
    """
    from ultralytics.utils import ASSETS

    from .recorder import ClipWriter

    src = cv2.imread(str(ASSETS / "bus.jpg"))
    found = detector.detect(src, track=False)  # type: ignore[attr-defined]
    if not found:
        raise RuntimeError("brak osob na obrazku wzorcowym - nie moge zbudowac wideo testowego")
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
    # stopy (dolna krawedz wyciecia) musza przejsc przez linie y = 0.62*360 = 223
    y_from, y_to = 10, h - ph - 8
    x = w // 2 - pw // 2
    for i in range(total):
        frame = np.full((h, w, 3), 110, np.uint8)
        frame += rng.integers(0, 5, (h, w, 3), dtype=np.uint8)
        cv2.rectangle(frame, (0, 300), (w, h), (85, 85, 85), -1)  # "podloga" dla kontekstu
        if i > fps:  # 1 s spokoju na nauczenie tla przez MOG2
            t = min(1.0, (i - fps) / max(1, total - fps - 1))
            y = int(y_from + (y_to - y_from) * t)
            frame[y : y + ph, x : x + pw] = crop
        writer.feed(frame, i / fps)
    return writer.close() or path


def selftest(cfg: Config) -> bool:
    """Sprawdza detektor, nagrywanie, caly pipeline i logike liczenia osob."""
    from .pipeline import Pipeline

    ok = True

    print("== 1/4 Detektor YOLO na obrazku wzorcowym")
    try:
        from ultralytics.utils import ASSETS

        from .detect import PersonDetector

        det = PersonDetector(cfg.cameras[0].detection)
        img = cv2.imread(str(ASSETS / "bus.jpg"))
        found = det.detect(img, track=False)
        print(f"   urzadzenie: {det.device}, wykryte osoby: {len(found)}")
        if len(found) < 3:
            print("   UWAGA: oczekiwano >=3 osob na bus.jpg")
            ok = False
        else:
            print("   OK")
    except Exception as exc:  # noqa: BLE001
        print(f"   BLAD: {exc}")
        return False

    print("\n== 2/4 Nagrywanie (ffmpeg -> H.264)")
    tmp = cfg.path("data/selftest")
    tmp.mkdir(parents=True, exist_ok=True)
    video = make_test_video(tmp / "synthetic.mp4")
    if video.exists() and video.stat().st_size > 1000:
        print(f"   OK: {video.name}, {video.stat().st_size/1024:.0f} kB")
    else:
        print("   BLAD: nie udalo sie zapisac wideo testowego")
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

    print("\n== 3/4 Pelny pipeline na wideo z ruchem")
    state, events, clips, _ = run_on(video, "motion")
    print(f"   przetworzone klatki: {state.frames_total}")
    print(f"   zdarzenia w bazie:   {len(events)}")
    print(f"   zapisane klipy:      {len(clips)}")
    if state.frames_total < 50:
        print("   BLAD: pipeline przetworzyl za malo klatek")
        ok = False
    elif not events:
        print("   BLAD: detekcja ruchu nie utworzyla zdarzenia")
        ok = False
    elif not clips:
        print("   BLAD: brak nagranego klipu")
        ok = False
    else:
        print(f"   OK: zdarzenie #{events[0].id} ({events[0].kind}), klip {events[0].clip_path}")

    print("\n== 4/4 Detekcja osoby, tracking i liczenie przejsc")
    try:
        person_video = make_person_video(tmp / "person.mp4", det)
        state, events, clips, counts = run_on(person_video, "person")
        person_events = [e for e in events if e.kind == "person"]
        total_crossings = counts.positive + counts.negative
        print(f"   unikalne tracki:     {counts.unique_seen}")
        print(f"   przekroczenia linii: {counts.positive} w dol / {counts.negative} w gore")
        print(f"   zdarzenia 'person':  {len(person_events)}")
        if not person_events:
            print("   BLAD: osoba nie zostala rozpoznana jako zdarzenie typu 'person'")
            ok = False
        elif counts.unique_seen == 0:
            print("   BLAD: tracker nie nadal zadnego ID")
            ok = False
        elif total_crossings != 1:
            print(f"   BLAD: oczekiwano dokladnie 1 przekroczenia, jest {total_crossings}")
            ok = False
        else:
            ev = person_events[0]
            print(f"   OK: zdarzenie #{ev.id}, max osob {ev.max_persons}, "
                  f"in={ev.count_in} out={ev.count_out}, miniatura {ev.snapshot_path}")
    except Exception as exc:  # noqa: BLE001
        print(f"   BLAD: {exc}")
        ok = False

    print("\n" + ("WSZYSTKO DZIALA" if ok else "SA PROBLEMY - patrz wyzej"))
    return ok
