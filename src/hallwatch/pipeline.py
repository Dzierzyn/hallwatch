"""Serce systemu: petla przetwarzania i maszyna stanow zdarzenia.

Przeplyw jednej klatki:

    kamera -> MASKA PRYWATNOSCI -> detektor ruchu -> (jesli ruch) YOLO+tracking
           -> licznik przejsc i stref -> nakladka -> bufor nagrania / stream / DB

Maska jest pierwsza w kolejnosci celowo: nic z zasloniętego obszaru nie dociera
do detekcji, nagrania ani chmury.

Maszyna stanow oszczedza CPU i dysk:
    IDLE   - tylko MOG2 (~1 ms/klatka), YOLO spi
    AWAKE  - ruch wybudzil detektor; YOLO + tracking na kazdej klatce
    EVENT  - trwa zdarzenie: leci nagranie z pre-rollem, zbieramy statystyki
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from . import draw
from .audio import AudioEvent, AudioMonitor
from .capture import FrameSource
from .cloud import CloudUploader
from .config import Config
from .counter import PeopleCounter
from .detect import Detection, PersonDetector
from .motion import MotionDetector
from .notify import Notifier
from .privacy import PrivacyMasker
from .recorder import EventRecorder
from .store import Store

log = logging.getLogger(__name__)


@dataclass
class LiveState:
    """Migawka stanu dla dashboardu (czytana z innego watku niz zapisywana)."""

    jpeg: bytes | None = None
    jpeg_seq: int = 0
    fps: float = 0.0
    persons: int = 0
    motion: bool = False
    motion_area: float = 0.0
    awake: bool = False
    recording: bool = False
    event_id: int | None = None
    count_in: int = 0
    count_out: int = 0
    unique_seen: int = 0
    zones: dict[str, int] = field(default_factory=dict)
    audio_dbfs: float = -120.0
    audio_loud: bool = False
    audio_status: str = "off"
    reconnects: int = 0
    frames_total: int = 0
    detect_ms: float = 0.0
    mode: str = "continuous"
    session_active: bool = False
    sessions: int = 0
    watch_until: float = 0.0
    started_at: float = field(default_factory=time.time)
    source: str = ""
    device: str = ""
    cloud_status: str = "off"
    last_error: str | None = None


class Pipeline:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.state = LiveState(source=cfg.camera.source, mode=cfg.camera.mode)
        self._state_lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

        self.store = Store(cfg.path(cfg.storage.db))
        self.masker = PrivacyMasker(cfg.privacy.masks)
        self.motion = MotionDetector(cfg.motion)
        self.counter = PeopleCounter(cfg.counting)
        self.recorder = EventRecorder(cfg.recording, cfg.path(cfg.recording.dir))
        self.notifier = Notifier(cfg.notify)
        self.uploader = CloudUploader(cfg.cloud, on_uploaded=self._on_uploaded).start()
        self.detector: PersonDetector | None = None

        self.audio = AudioMonitor(cfg.camera.source, cfg.audio, on_event=self._on_audio_event)

        # stan zdarzenia
        self._event_id: int | None = None
        self._event_started: float = 0.0
        self._last_activity: float = 0.0
        self._event_max_persons: int = 0
        self._event_in: int = 0
        self._event_out: int = 0
        self._event_peak_dbfs: float | None = None
        self._best_frame: np.ndarray | None = None
        self._best_persons: int = -1
        self._awake_until: float = 0.0
        self._watch_until: float = 0.0  # dopoki teraz < tego, ktos patrzy
        self._session_started: float = 0.0

        # bufor statystyk minutowych (zapis do DB raz na sekunde, nie co klatke)
        self._pending = {"frames": 0, "motion": 0, "persons": 0, "in": 0, "out": 0}
        self._last_flush = time.time()
        self._fps_ema = 0.0

    # -- cykl zycia ---------------------------------------------------------
    def start(self) -> "Pipeline":
        self._thread = threading.Thread(target=self._guarded_run, name="pipeline", daemon=True)
        self._thread.start()
        return self

    def _guarded_run(self) -> None:
        try:
            self.run()
        except Exception as exc:  # noqa: BLE001
            log.exception("Pipeline padl: %s", exc)
            with self._state_lock:
                self.state.last_error = str(exc)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10.0)
        self.audio.stop()
        self._close_event(time.time(), reason="shutdown")
        self.uploader.stop()
        self.store.close()

    # -- petla glowna -------------------------------------------------------
    def run(self) -> None:
        cfg = self.cfg
        self.detector = PersonDetector(cfg.detection)
        with self._state_lock:
            self.state.device = self.detector.device
            self.state.cloud_status = (
                "on" if self.uploader.enabled else (self.uploader.error or "off")
            )

        self.audio.start()
        if cfg.audio.enabled:
            with self._state_lock:
                self.state.audio_status = self.audio.error or "on"

        if cfg.camera.mode == "on_demand":
            self._run_on_demand()
        else:
            self._run_continuous()

    def _open_source(self) -> FrameSource:
        cfg = self.cfg
        return FrameSource(
            cfg.camera.source,
            width=cfg.camera.width,
            fps_limit=cfg.camera.fps_limit,
            reconnect_delay_s=cfg.camera.reconnect_delay_s,
        ).start()

    def _pump(self, source: FrameSource, deadline: float | None = None) -> str:
        """Przetwarza klatki, dopoki jest po co. Zwraca powod wyjscia."""
        cfg = self.cfg
        min_interval = 1.0 / cfg.camera.fps_limit if cfg.camera.fps_limit else 0.0
        last_loop = 0.0

        while not self._stop.is_set():
            if min_interval:
                sleep_for = min_interval - (time.time() - last_loop)
                if sleep_for > 0:
                    time.sleep(sleep_for)
            last_loop = time.time()

            frame, ts = source.read(timeout=5.0)
            if frame is None:
                if source.finished:
                    return "eof"
                continue

            self._process_frame(frame, ts, source)

            if deadline is not None:
                if ts < self._watch_until:
                    # ktos patrzy - nie rozlaczaj sie, choc w kadrze cisza.
                    # Bezpiecznik chroni bateria przed zapomniana zakladka.
                    if ts - self._session_started > cfg.camera.watch_max_s:
                        return "watch-limit"
                elif ts > deadline:
                    return "limit"
                elif ts - self._last_activity > cfg.camera.session_idle_s:
                    return "idle"
        return "stop"

    def _run_continuous(self) -> None:
        log.info("Pipeline wystartowal w trybie continuous (zrodlo=%s)", self.cfg.camera.source)
        source = self._open_source()
        try:
            reason = self._pump(source)
            if reason == "eof":
                log.info("Koniec pliku wideo - zamykam pipeline")
        finally:
            source.stop()
            self._close_event(time.time(), reason="stop")

    # -- tryb bateryjny -----------------------------------------------------
    def _run_on_demand(self) -> None:
        """Strumien otwierany dopiero na sygnal wybudzenia.

        Kamera na baterii nie przezyje ciaglego streamu - utrzymanie polaczenia
        RTSP nie pozwala jej zasnac i zjada akumulator w kilka dni. Tutaj
        czekamy na sygnal (webhook /api/wake, 'hallwatch wake', automatyzacja
        z huba), lapiemy krotka sesje i rozlaczamy sie, zeby kamera wrocila do
        snu.
        """
        cfg = self.cfg
        log.info(
            "Pipeline wystartowal w trybie on_demand (sesja max %.0fs, cisza %.0fs%s)",
            cfg.camera.session_seconds,
            cfg.camera.session_idle_s,
            f", godziny {cfg.camera.active_hours}" if cfg.camera.active_hours else "",
        )
        while not self._stop.is_set():
            if not self._wake.wait(timeout=1.0):
                continue
            self._wake.clear()
            if not self._in_active_window():
                log.info("Wybudzenie poza godzinami aktywnosci - ignoruje")
                continue
            self._session()

    def _session(self) -> None:
        cfg = self.cfg
        started = time.time()
        self._session_started = started
        log.info("Sesja: lacze sie ze zrodlem")
        # nowy model tla na kazda sesje - stary jest bezuzyteczny po przerwie,
        # a warmup skrocony, bo o zdarzeniu wiemy z sygnalu wybudzenia
        self.motion = MotionDetector(cfg.motion.model_copy(update={"warmup_frames": 3}))
        self._last_activity = started
        with self._state_lock:
            self.state.session_active = True
            self.state.sessions += 1

        source = self._open_source()
        try:
            reason = self._pump(source, deadline=started + cfg.camera.session_seconds)
        finally:
            source.stop()
            self._close_event(time.time(), reason="session-end")
            with self._state_lock:
                self.state.session_active = False
        log.info("Sesja zakonczona po %.1fs (%s) - kamera moze zasnac",
                 time.time() - started, reason)

    def _in_active_window(self, now: float | None = None) -> bool:
        window = self.cfg.camera.active_hours
        if not window:
            return True
        try:
            start_s, end_s = (part.strip() for part in window.split("-", 1))
            sh, sm = (int(v) for v in start_s.split(":"))
            eh, em = (int(v) for v in end_s.split(":"))
        except ValueError:
            log.warning("Nie rozumiem camera.active_hours=%r - ignoruje ograniczenie", window)
            return True
        lt = time.localtime(now if now is not None else time.time())
        minutes = lt.tm_hour * 60 + lt.tm_min
        start, end = sh * 60 + sm, eh * 60 + em
        return start <= minutes < end if start <= end else (minutes >= start or minutes < end)

    def wake(self, source: str = "api") -> bool:
        """Zglasza wybudzenie. Zwraca False, gdy tryb continuous (nic nie robi)."""
        if self.cfg.camera.mode != "on_demand":
            return False
        log.info("Sygnal wybudzenia (%s)", source)
        self._wake.set()
        return True

    def hold_session(self) -> dict:
        """Puls z dashboardu: 'patrze, nie rozlaczaj sie'.

        Wolane cyklicznie, dopoki karta przegladarki jest widoczna. Jesli sesji
        nie ma, budzi kamere; jesli jest, przedluza ja o watch_hold_s.
        """
        cfg = self.cfg
        if cfg.camera.mode != "on_demand":
            return {"holding": False, "detail": "tryb continuous - strumien jest ciagly"}

        now = time.time()
        self._watch_until = now + cfg.camera.watch_hold_s
        with self._state_lock:
            active = self.state.session_active
            self.state.watch_until = self._watch_until
        if not active:
            self._wake.set()
        remaining = max(0.0, cfg.camera.watch_max_s - (now - self._session_started)) if active else cfg.camera.watch_max_s
        return {
            "holding": True,
            "session_active": active,
            "hold_s": cfg.camera.watch_hold_s,
            "budget_left_s": round(remaining, 1),
        }

    def _process_frame(self, frame: np.ndarray, ts: float, source: FrameSource) -> None:
        cfg = self.cfg

        # 1. prywatnosc PRZED czymkolwiek innym
        frame = self.masker.apply(frame)

        # 2. tani straznik
        motion = self.motion.update(frame)

        # 3. detekcja tylko gdy jest po co
        detections: list[Detection] = []
        detect_ms = 0.0
        if motion.detected:
            self._awake_until = ts + cfg.detection.awake_seconds
        awake = ts < self._awake_until
        if awake and self.detector is not None:
            t0 = time.perf_counter()
            detections = self.detector.detect(frame, track=True)
            detect_ms = (time.perf_counter() - t0) * 1000.0

        # detekcje, ktorych kotwica wpada w maske prywatnosci - odrzucamy
        # (martwa sciezka, dopoki privacy.masks jest puste)
        if self.masker.active and detections:
            anchor_mode = cfg.counting.anchor
            detections = [
                d
                for d in detections
                if not self.masker.contains(d.anchor(anchor_mode), frame.shape)
            ]

        # 4. liczenie
        crossings = self.counter.update(detections, frame.shape, ts)
        persons = len(detections)

        # 5. maszyna stanow zdarzenia
        activity = motion.detected or persons > 0
        if activity:
            self._last_activity = ts
        self._update_event(frame, ts, activity, persons, crossings)

        # 6. nakladka
        annotated = frame.copy()
        draw.draw_zones(annotated, self.counter)
        draw.draw_line(annotated, self.counter)
        if motion.detected and not detections:
            draw.draw_motion(annotated, motion.boxes)
        draw.draw_trails(annotated, self.counter)
        draw.draw_detections(
            annotated,
            detections,
            self.detector.names if self.detector else {},
            anchor_mode=cfg.counting.anchor,
        )

        fps = self._fps(ts)
        hud = [
            (f"FPS {fps:4.1f}   osoby {persons}", draw.WHITE),
            (
                f"{'AWAKE' if awake else 'IDLE '}  YOLO {detect_ms:5.1f}ms  ruch {motion.area_frac*100:4.1f}%",
                draw.GREEN if awake else draw.GREY,
            ),
            (
                f"{self.counter.cfg.direction_labels.positive} {self.counter.state.positive}"
                f"  {self.counter.cfg.direction_labels.negative} {self.counter.state.negative}"
                f"  unikalne {self.counter.state.unique_seen}",
                draw.YELLOW,
            ),
        ]
        if self.cfg.audio.enabled and self.audio.available:
            hud.append(
                (
                    f"audio {self.audio.level_dbfs:6.1f} dBFS",
                    draw.RED if self.audio.loud else draw.BLUE,
                )
            )
        draw.draw_hud(annotated, hud)
        draw.draw_timestamp(annotated, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)))
        if self.recorder.active:
            draw.draw_rec_indicator(annotated)

        # 7. nagranie: czysta klatka (zamaskowana) albo z wypalona nakladka
        self.recorder.push(annotated if cfg.recording.burn_overlay else frame, ts)

        # kadr "najlepszy" do miniatury zdarzenia = najwiecej osob naraz
        if self._event_id is not None and persons >= self._best_persons:
            self._best_persons = persons
            self._best_frame = annotated.copy()

        # 8. publikacja stanu
        ok, buf = cv2.imencode(
            ".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), cfg.web.stream_quality]
        )
        with self._state_lock:
            s = self.state
            if ok:
                s.jpeg = buf.tobytes()
                s.jpeg_seq += 1
            s.fps = fps
            s.persons = persons
            s.motion = motion.detected
            s.motion_area = motion.area_frac
            s.awake = awake
            s.recording = self.recorder.active
            s.event_id = self._event_id
            s.count_in = self.counter.state.positive
            s.count_out = self.counter.state.negative
            s.unique_seen = self.counter.state.unique_seen
            s.zones = dict(self.counter.state.zone_counts)
            s.audio_dbfs = self.audio.level_dbfs
            s.audio_loud = self.audio.loud
            s.reconnects = source.reconnects
            s.frames_total += 1
            s.detect_ms = detect_ms

        # 9. statystyki minutowe
        self._pending["frames"] += 1
        self._pending["motion"] += int(motion.detected)
        self._pending["persons"] = max(self._pending["persons"], persons)
        for c in crossings:
            key = "in" if c.sign > 0 else "out"
            self._pending[key] += 1
        if ts - self._last_flush >= 1.0:
            self.store.bump_minute(
                ts,
                frames=self._pending["frames"],
                motion=self._pending["motion"],
                persons=self._pending["persons"],
                count_in=self._pending["in"],
                count_out=self._pending["out"],
            )
            self._pending = {"frames": 0, "motion": 0, "persons": 0, "in": 0, "out": 0}
            self._last_flush = ts

    def _fps(self, ts: float) -> float:
        prev = getattr(self, "_last_ts", None)
        self._last_ts = ts
        if prev is None or ts <= prev:
            return self._fps_ema
        inst = 1.0 / (ts - prev)
        self._fps_ema = inst if self._fps_ema == 0 else 0.9 * self._fps_ema + 0.1 * inst
        return self._fps_ema

    # -- zdarzenia ----------------------------------------------------------
    def _update_event(
        self,
        frame: np.ndarray,
        ts: float,
        activity: bool,
        persons: int,
        crossings: list,
    ) -> None:
        if self._event_id is None:
            if activity:
                self._open_event(ts, kind="person" if persons else "motion")
        else:
            self._event_max_persons = max(self._event_max_persons, persons)
            quiet_for = ts - self._last_activity
            if quiet_for > self.cfg.recording.post_roll_s:
                self._close_event(ts, reason="quiet")

        for c in crossings:
            self.store.add_crossing(self._event_id, c.ts, c.track_id, c.direction)
            if c.sign > 0:
                self._event_in += 1
            else:
                self._event_out += 1
            log.info("Przekroczenie linii: track #%d -> %s", c.track_id, c.direction)

    def _open_event(self, ts: float, kind: str) -> None:
        self._event_id = self.store.open_event(kind, ts, meta={"camera": self.cfg.camera.name})
        self._event_started = ts
        self._event_max_persons = 0
        self._event_in = 0
        self._event_out = 0
        self._event_peak_dbfs = None
        self._best_frame = None
        self._best_persons = -1
        self.recorder.start(ts)
        log.info("Zdarzenie #%s otwarte (%s)", self._event_id, kind)

    def _close_event(self, ts: float, reason: str = "quiet") -> None:
        if self._event_id is None:
            return
        event_id = self._event_id
        self._event_id = None

        clip = self.recorder.stop()
        snapshot = None
        if self._best_frame is not None:
            snapshot = self.recorder.snapshot(self._best_frame, self._event_started)

        kind = "person" if self._event_max_persons > 0 else "motion"
        if self._event_peak_dbfs is not None and self._event_max_persons == 0:
            kind = "audio"

        self.store.close_event(
            event_id,
            ended_at=ts,
            kind=kind,
            max_persons=self._event_max_persons,
            count_in=self._event_in,
            count_out=self._event_out,
            peak_dbfs=self._event_peak_dbfs,
            clip_path=str(clip.relative_to(self.cfg.root)) if clip else None,
            snapshot_path=str(snapshot.relative_to(self.cfg.root)) if snapshot else None,
        )
        log.info(
            "Zdarzenie #%d zamkniete (%s, %.1fs, osob max %d, %s)",
            event_id, reason, ts - self._event_started, self._event_max_persons,
            clip.name if clip else "bez klipu",
        )

        for media in (clip, snapshot):
            if media is not None:
                self.uploader.enqueue(event_id, media)

        if self._event_max_persons > 0:
            labels = self.counter.cfg.direction_labels
            self.notifier.send_async(
                f"{self.cfg.camera.name}: {self._event_max_persons} os.",
                f"Zdarzenie #{event_id}, czas {ts - self._event_started:.0f}s, "
                f"{labels.positive}: {self._event_in}, {labels.negative}: {self._event_out}",
                "default",
                "walking",
                snapshot,
            )

    def _on_audio_event(self, ev: AudioEvent) -> None:
        """Callback z watku audio - dolacza szczyt do trwajacego zdarzenia albo tworzy nowe."""
        log.info("Zdarzenie audio: %.1f dBFS, %.1fs", ev.peak_dbfs, ev.duration_s)
        if self._event_id is not None:
            self._event_peak_dbfs = max(self._event_peak_dbfs or -120.0, ev.peak_dbfs)
            return
        event_id = self.store.open_event("audio", ev.started_at, meta={"camera": self.cfg.camera.name})
        self.store.close_event(
            event_id, ended_at=ev.ended_at, peak_dbfs=ev.peak_dbfs, kind="audio"
        )
        self.notifier.send_async(
            f"{self.cfg.camera.name}: dzwiek",
            f"Szczyt {ev.peak_dbfs:.1f} dBFS przez {ev.duration_s:.1f}s",
            "default",
            "loud_sound",
        )

    def _on_uploaded(self, event_id: int, key: str) -> None:
        if event_id:
            self.store.close_event(event_id, cloud_key=key)

    # -- dostep dla web -----------------------------------------------------
    def snapshot_state(self) -> LiveState:
        with self._state_lock:
            s = self.state
            return LiveState(**{**s.__dict__, "jpeg": None})

    def latest_jpeg(self) -> tuple[bytes | None, int]:
        with self._state_lock:
            return self.state.jpeg, self.state.jpeg_seq


def prune(cfg: Config) -> tuple[int, int]:
    """Usuwa media starsze niz recording.retention_days. Zwraca (pliki, zdarzenia)."""
    store = Store(cfg.path(cfg.storage.db))
    cutoff = time.time() - cfg.recording.retention_days * 86400
    removed_files = 0
    events = store.events_older_than(cutoff)
    for ev in events:
        for rel in (ev.clip_path, ev.snapshot_path):
            if not rel:
                continue
            p = cfg.path(rel)
            if p.exists():
                p.unlink()
                removed_files += 1
        store.clear_media(ev.id)
    store.close()
    return removed_files, len(events)
