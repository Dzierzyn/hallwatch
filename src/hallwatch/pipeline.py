"""The heart of the system: the processing loop and the event state machine.

The path of a single frame:

    camera -> PRIVACY MASK -> motion detector -> (if motion) YOLO + tracking
           -> line and zone counter -> overlay -> recording buffer / stream / DB

The mask comes first on purpose: nothing from a masked area reaches detection,
recording or the cloud.

The state machine saves CPU and disk:
    IDLE   - MOG2 only (~1 ms/frame), YOLO asleep
    AWAKE  - motion woke the detector; YOLO plus tracking on every frame
    EVENT  - an event is in progress: recording with pre-roll, statistics collected
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from . import draw
from .audio import AudioEvent, AudioMonitor
from .capture import FrameSource
from .cloud import CloudUploader
from .config import CameraProfile, Config
from .counter import PeopleCounter
from .detect import Detection, PersonDetector
from .motion import MotionDetector
from .notify import Notifier
from .privacy import PrivacyMasker
from .recorder import EventRecorder
from .store import Store

log = logging.getLogger(__name__)

# klasy COCO traktowane zbiorczo jako "pojazd" w statystykach ruchu
VEHICLE_CLASSES = {"car", "motorcycle", "bus", "truck", "bicycle", "train"}


@dataclass
class LiveState:
    """A state snapshot for the dashboard (read from a different thread than it is written)."""

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
    camera: str = ""
    mode: str = "continuous"
    sampling_window: bool = False
    session_active: bool = False
    sessions: int = 0
    watch_until: float = 0.0
    started_at: float = field(default_factory=time.time)
    source: str = ""
    device: str = ""
    cloud_status: str = "off"
    last_error: str | None = None


class Pipeline:
    def __init__(
        self,
        cfg: Config,
        profile: CameraProfile | None = None,
        store: Store | None = None,
        uploader: CloudUploader | None = None,
        notifier: Notifier | None = None,
    ) -> None:
        self.cfg = cfg
        self.prof = profile or cfg.cameras[0]
        prof = self.prof
        self.state = LiveState(source=prof.source, mode=prof.mode, camera=prof.name)
        self._state_lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

        # the database, cloud and notifications are SHARED across all cameras: one
        # event timeline and one upload queue. The manager injects them;
        # when running a single camera we create our own.
        self._owns_shared = store is None
        self.store = store or Store(cfg.path(cfg.storage.db))
        self.notifier = notifier or Notifier(cfg.notify)
        self.uploader = uploader or CloudUploader(cfg.cloud, on_uploaded=self._on_uploaded).start()

        self.masker = PrivacyMasker(prof.privacy.masks)
        self.motion = MotionDetector(prof.motion)
        self.counter = PeopleCounter(prof.counting)
        self.recorder = EventRecorder(prof.recording, cfg.path(prof.clip_dir))
        self.detector: PersonDetector | None = None
        self.audio = AudioMonitor(prof.source, prof.audio, on_event=self._on_audio_event)

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
        self._event_classes: Counter[str] = Counter()
        self._awake_until: float = 0.0
        self._watch_until: float = 0.0
        self._session_started: float = 0.0
        self._sampling_window: bool = False

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
        if self._owns_shared:
            self.uploader.stop()
            self.store.close()

    # -- petla glowna -------------------------------------------------------
    def run(self) -> None:
        cfg, prof = self.cfg, self.prof
        self.detector = PersonDetector(self.prof.detection)
        with self._state_lock:
            self.state.device = self.detector.device
            self.state.cloud_status = (
                "on" if self.uploader.enabled else (self.uploader.error or "off")
            )

        self.audio.start()
        if prof.audio.enabled:
            with self._state_lock:
                self.state.audio_status = self.audio.error or "on"

        match prof.mode:
            case "on_demand":
                self._run_on_demand()
            case "sampling":
                self._run_sampling()
            case _:
                self._run_continuous()

    def _open_source(self) -> FrameSource:
        cfg, prof = self.cfg, self.prof
        return FrameSource(
            prof.source,
            width=prof.width,
            fps_limit=prof.fps_limit,
            reconnect_delay_s=prof.reconnect_delay_s,
        ).start()

    def _pump(
        self, source: FrameSource, deadline: float | None = None, idle_exit: bool = True
    ) -> str:
        """Processes frames for as long as there is a reason to. Returns the exit reason."""
        cfg, prof = self.cfg, self.prof
        min_interval = 1.0 / prof.fps_limit if prof.fps_limit else 0.0
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
                    # someone is watching - do not disconnect, even with a quiet frame.
                    # Bezpiecznik chroni bateria przed zapomniana zakladka.
                    if ts - self._session_started > prof.watch_max_s:
                        return "watch-limit"
                elif ts > deadline:
                    return "limit"
                elif idle_exit and ts - self._last_activity > prof.session_idle_s:
                    return "idle"
        return "stop"

    def _run_continuous(self) -> None:
        prof = self.prof
        log.info("Pipeline wystartowal w trybie continuous (zrodlo=%s)", self.prof.source)
        source = self._open_source()
        try:
            reason = self._pump(source)
            if reason == "eof":
                log.info("End of video file, closing the pipeline")
        finally:
            source.stop()
            self._close_event(time.time(), reason="stop")

    # -- tryb bateryjny -----------------------------------------------------
    def _run_on_demand(self) -> None:
        """The stream is opened only on a wake signal.

        A battery camera will not survive a continuous stream: holding an RTSP
        connection open stops it from sleeping and drains the battery in days. Here we
        wait for a signal (the /api/wake webhook, 'hallwatch wake', an automation from
        the hub), take a short session and disconnect so the camera can go back to
        sleep.
        """
        cfg, prof = self.cfg, self.prof
        log.info(
            "Pipeline started in on_demand mode (session max %.0fs, idle %.0fs%s)",
            prof.session_seconds,
            prof.session_idle_s,
            f", godziny {prof.active_hours}" if prof.active_hours else "",
        )
        while not self._stop.is_set():
            if not self._wake.wait(timeout=1.0):
                continue
            self._wake.clear()
            if not self._in_active_window():
                log.info("Wybudzenie poza godzinami aktywnosci - ignoruje")
                continue
            self._session()

    def _session(
        self, max_seconds: float | None = None, idle_exit: bool = True, tag: str = "Session"
    ) -> None:
        cfg, prof = self.cfg, self.prof
        started = time.time()
        self._session_started = started
        log.info("%s: lacze sie ze zrodlem", tag)
        # a fresh background model for each session - the old one is useless after a break,
        # and the warmup is shortened, because the wake signal already told us about the event
        self.motion = MotionDetector(prof.motion.model_copy(update={"warmup_frames": 3}))
        self._last_activity = started
        with self._state_lock:
            self.state.session_active = True
            self.state.sessions += 1

        source = self._open_source()
        try:
            limit = max_seconds if max_seconds is not None else prof.session_seconds
            reason = self._pump(source, deadline=started + limit, idle_exit=idle_exit)
        finally:
            source.stop()
            self._close_event(time.time(), reason="session-end")
            with self._state_lock:
                self.state.session_active = False
        log.info("%s ended after %.1fs (%s), the camera may sleep",
                 tag, time.time() - started, reason)

    # -- tryb probkowania (statystyki ruchu) --------------------------------
    def _run_sampling(self) -> None:
        """Observes a short window at a fixed interval and extrapolates.

        Counting vehicles off a motion trigger would produce garbage: PIR misses most
        cars and there is no way to know which ones, so the numbers would mean nothing.
        Sampling is more honest: we know exactly what fraction of the time we were
        watching (the duty cycle), and every event carries a multiplier for
        extrapolation.

        Windows are aligned to the clock by default, so that samples from different
        days cover the same parts of the hour, otherwise comparisons across days would
        mix the peak with the quiet.
        """
        prof = self.prof
        s = prof.sampling
        log.info(
            "Pipeline started in sampling mode: %.0fs every %.0f min (we see %.1f%% of the time)",
            s.seconds, s.every_minutes, s.duty_cycle * 100,
        )
        first = True
        while not self._stop.is_set():
            if not first:
                wait_s = self._seconds_to_next_window()
                log.info("Next window in %.0fs, the camera is asleep", wait_s)
                if self._stop.wait(wait_s):
                    break
            first = False
            if not self._in_active_window():
                continue
            self._sampling_window = True
            try:
                self._session(max_seconds=s.seconds, idle_exit=False, tag="Sampling window")
            finally:
                self._sampling_window = False

    def _seconds_to_next_window(self) -> float:
        period = max(60.0, self.prof.sampling.every_minutes * 60.0)
        if not self.prof.sampling.align_to_clock:
            return period
        return period - (time.time() % period)

    def _in_active_window(self, now: float | None = None) -> bool:
        prof = self.prof
        window = self.prof.active_hours
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
        """Reports a wake-up. Returns False in continuous mode (where it does nothing)."""
        prof = self.prof
        if self.prof.mode != "on_demand":
            return False
        log.info("Sygnal wybudzenia (%s)", source)
        self._wake.set()
        return True

    def hold_session(self) -> dict:
        """A heartbeat from the dashboard: 'I am watching, do not disconnect'.

        Called periodically for as long as the browser tab is visible. If there is no
        session it wakes the camera; if there is, it extends it by watch_hold_s.
        """
        cfg, prof = self.cfg, self.prof
        if prof.mode != "on_demand":
            return {"holding": False, "detail": "tryb continuous - strumien jest ciagly"}

        now = time.time()
        self._watch_until = now + prof.watch_hold_s
        with self._state_lock:
            active = self.state.session_active
            self.state.watch_until = self._watch_until
        if not active:
            self._wake.set()
        remaining = max(0.0, prof.watch_max_s - (now - self._session_started)) if active else prof.watch_max_s
        return {
            "holding": True,
            "session_active": active,
            "hold_s": prof.watch_hold_s,
            "budget_left_s": round(remaining, 1),
        }

    def _process_frame(self, frame: np.ndarray, ts: float, source: FrameSource) -> None:
        cfg, prof = self.cfg, self.prof

        # 1. prywatnosc PRZED czymkolwiek innym
        frame = self.masker.apply(frame)

        # 2. tani straznik
        motion = self.motion.update(frame)

        # 3. detection only when there is a reason for it
        detections: list[Detection] = []
        detect_ms = 0.0
        if motion.detected:
            self._awake_until = ts + prof.detection.awake_seconds
        awake = ts < self._awake_until
        if awake and self.detector is not None:
            t0 = time.perf_counter()
            detections = self.detector.detect(frame, track=True)
            detect_ms = (time.perf_counter() - t0) * 1000.0

        # detekcje, ktorych kotwica wpada w maske prywatnosci - odrzucamy
        # (dead path for as long as privacy.masks is empty)
        if self.masker.active and detections:
            anchor_mode = prof.counting.anchor
            detections = [
                d
                for d in detections
                if not self.masker.contains(d.anchor(anchor_mode), frame.shape)
            ]

        # 4. liczenie
        crossings = self.counter.update(detections, frame.shape, ts)
        persons = len(detections)
        if detections and self.detector is not None:
            for d in detections:
                self._event_classes[self.detector.names.get(d.cls, str(d.cls))] += 1

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
            anchor_mode=prof.counting.anchor,
        )

        fps = self._fps(ts)
        hud = [
            (f"FPS {fps:4.1f}   people {persons}", draw.WHITE),
            (
                f"{'AWAKE' if awake else 'IDLE '}  YOLO {detect_ms:5.1f}ms  motion {motion.area_frac*100:4.1f}%",
                draw.GREEN if awake else draw.GREY,
            ),
            (
                f"{self.counter.cfg.direction_labels.positive} {self.counter.state.positive}"
                f"  {self.counter.cfg.direction_labels.negative} {self.counter.state.negative}"
                f"  unique {self.counter.state.unique_seen}",
                draw.YELLOW,
            ),
        ]
        if self.prof.audio.enabled and self.audio.available:
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

        # 7. recording: a clean (masked) frame, or one with the overlay burned in
        self.recorder.push(annotated if prof.recording.burn_overlay else frame, ts)

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
            s.sampling_window = self._sampling_window
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
                camera=prof.name,
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
        prof = self.prof
        if self._event_id is None:
            if activity:
                self._open_event(ts, kind="person" if persons else "motion")
        else:
            self._event_max_persons = max(self._event_max_persons, persons)
            quiet_for = ts - self._last_activity
            if quiet_for > self.prof.recording.post_roll_s:
                self._close_event(ts, reason="quiet")

        for c in crossings:
            self.store.add_crossing(self._event_id, c.ts, c.track_id, c.direction)
            if c.sign > 0:
                self._event_in += 1
            else:
                self._event_out += 1
            log.info("Line crossing: track #%d -> %s", c.track_id, c.direction)

    def _open_event(self, ts: float, kind: str) -> None:
        prof = self.prof
        sampled = self._sampling_window
        self._event_id = self.store.open_event(
            kind,
            ts,
            camera=prof.name,
            sampled=sampled,
            sample_weight=(1.0 / prof.sampling.duty_cycle) if sampled else 1.0,
            meta={"mode": prof.mode},
        )
        self._event_started = ts
        self._event_max_persons = 0
        self._event_in = 0
        self._event_out = 0
        self._event_peak_dbfs = None
        self._best_frame = None
        self._best_persons = -1
        self._event_classes = Counter()
        self.recorder.start(ts)
        log.info("Event #%s opened (%s)", self._event_id, kind)

    def _close_event(self, ts: float, reason: str = "quiet") -> None:
        prof = self.prof
        if self._event_id is None:
            return
        event_id = self._event_id
        self._event_id = None

        clip = self.recorder.stop()
        snapshot = None
        if self._best_frame is not None:
            snapshot = self.recorder.snapshot(self._best_frame, self._event_started)

        kind = self._event_kind()

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
            "Event #%d closed (%s, %.1fs, max people %d, %s)",
            event_id, reason, ts - self._event_started, self._event_max_persons,
            clip.name if clip else "bez klipu",
        )

        for media in (clip, snapshot):
            if media is not None:
                self.uploader.enqueue(event_id, media)

        if self._event_max_persons > 0:
            labels = self.counter.cfg.direction_labels
            self.notifier.send_async(
                f"{self.prof.name}: {self._event_max_persons} os.",
                f"Event #{event_id}, time {ts - self._event_started:.0f}s, "
                f"{labels.positive}: {self._event_in}, {labels.negative}: {self._event_out}",
                "default",
                "walking",
                snapshot,
            )

    def _event_kind(self) -> str:
        """The event kind comes from the classes actually detected, not from an assumption.

        The street camera counts vehicles and the corridor camera counts people.
        Recording everything as 'person' would falsify the data at the source, and the
        analytics layer would have no way to undo it.
        """
        if self._event_max_persons == 0:
            return "audio" if self._event_peak_dbfs is not None else "motion"
        top = self._event_classes.most_common(1)
        if not top:
            return "motion"
        name = top[0][0]
        if name == "person":
            return "person"
        if name in VEHICLE_CLASSES:
            return "vehicle"
        return name

    def _on_audio_event(self, ev: AudioEvent) -> None:
        """Callback z watku audio - dolacza szczyt do trwajacego zdarzenia albo tworzy nowe."""
        prof = self.prof
        log.info("Audio event: %.1f dBFS, %.1fs", ev.peak_dbfs, ev.duration_s)
        if self._event_id is not None:
            self._event_peak_dbfs = max(self._event_peak_dbfs or -120.0, ev.peak_dbfs)
            return
        event_id = self.store.open_event("audio", ev.started_at, meta={"camera": self.prof.name})
        self.store.close_event(
            event_id, ended_at=ev.ended_at, peak_dbfs=ev.peak_dbfs, kind="audio"
        )
        self.notifier.send_async(
            f"{self.prof.name}: audio",
            f"Szczyt {ev.peak_dbfs:.1f} dBFS przez {ev.duration_s:.1f}s",
            "default",
            "loud_sound",
        )

    def _on_uploaded(self, event_id: int, key: str) -> None:
        if event_id:
            self.store.close_event(event_id, cloud_key=key)

    # -- access for the web layer -------------------------------------------
    def snapshot_state(self) -> LiveState:
        with self._state_lock:
            s = self.state
            return LiveState(**{**s.__dict__, "jpeg": None})

    def latest_jpeg(self) -> tuple[bytes | None, int]:
        with self._state_lock:
            return self.state.jpeg, self.state.jpeg_seq


class PipelineManager:
    """One Pipeline per camera, with a SHARED database, cloud and ntfy.

    Sharing is not an optimisation but a correctness requirement: events from the
    corridor and from the street have to sit on one timeline in one database,
    otherwise the analytics layer cannot join them into a single picture. The
    upload queue is single too, so that two cameras do not fight over the link.
    """

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.store = Store(cfg.path(cfg.storage.db))
        self.notifier = Notifier(cfg.notify)
        self.uploader = CloudUploader(cfg.cloud, on_uploaded=self._on_uploaded).start()
        self.pipelines: dict[str, Pipeline] = {
            prof.slug: Pipeline(cfg, prof, self.store, self.uploader, self.notifier)
            for prof in cfg.cameras
        }

    def _on_uploaded(self, event_id: int, key: str) -> None:
        if event_id:
            self.store.close_event(event_id, cloud_key=key)

    def start(self) -> "PipelineManager":
        for slug, pipe in self.pipelines.items():
            log.info("Starting camera '%s' (%s)", pipe.prof.name, pipe.prof.mode)
            pipe.start()
        return self

    def stop(self) -> None:
        for pipe in self.pipelines.values():
            pipe.stop()
        self.uploader.stop()
        self.store.close()

    def get(self, slug: str | None = None) -> Pipeline:
        if slug is None:
            return next(iter(self.pipelines.values()))
        if slug not in self.pipelines:
            raise KeyError(slug)
        return self.pipelines[slug]

    @property
    def slugs(self) -> list[str]:
        return list(self.pipelines)


def prune(cfg: Config) -> tuple[int, int]:
    """Removes media older than retention_days. Returns (files, events).

    Retention is set per camera, so every event is judged against the threshold of
    ITS OWN camera, otherwise the street's shorter retention would delete corridor
    recordings.
    """
    store = Store(cfg.path(cfg.storage.db))
    now = time.time()
    by_camera = {cam.name: cam.recording.retention_days for cam in cfg.cameras}
    default_days = min(by_camera.values())
    removed_files = 0
    events = store.events_older_than(now - min(by_camera.values()) * 86400)
    touched = 0
    for ev in events:
        days = by_camera.get(ev.camera, default_days)
        if ev.started_at >= now - days * 86400:
            continue  # this camera holds it longer
        touched += 1
        for rel in (ev.clip_path, ev.snapshot_path):
            if not rel:
                continue
            p = cfg.path(rel)
            if p.exists():
                p.unlink()
                removed_files += 1
        store.clear_media(ev.id)
    store.close()
    return removed_files, touched
