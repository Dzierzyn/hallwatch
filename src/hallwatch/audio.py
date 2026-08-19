"""Audio detection from the RTSP stream (a separate ffmpeg process -> raw PCM).

Why separate from video: OpenCV does not expose audio from RTSP at all. So we take
the same URL, tell ffmpeg "-vn" (no video) and get a stream of 16-bit samples that
we process in numpy.

The level is computed in dBFS (0 dB = maximum amplitude, values are negative), with
hysteresis: one threshold to RAISE the alarm and a different one to clear it.
Without that, sound oscillating around the threshold would generate hundreds of
events per second.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from .config import AudioCfg

log = logging.getLogger(__name__)

INT16_FULL_SCALE = 32768.0


@dataclass
class AudioEvent:
    started_at: float
    ended_at: float
    peak_dbfs: float

    @property
    def duration_s(self) -> float:
        return self.ended_at - self.started_at


def dbfs(samples: np.ndarray) -> float:
    if samples.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean((samples.astype(np.float32) / INT16_FULL_SCALE) ** 2)))
    return 20.0 * np.log10(max(rms, 1e-9))


class AudioMonitor:
    """A thread listening to the sound level; calls the callback when an event ends."""

    def __init__(
        self,
        rtsp_url: str,
        cfg: AudioCfg,
        on_event: Callable[[AudioEvent], None] | None = None,
    ) -> None:
        self.url = rtsp_url
        self.cfg = cfg
        self.on_event = on_event
        self.level_dbfs: float = -120.0
        self.peak_dbfs: float = -120.0
        self.loud = False
        self.available = False
        self.error: str | None = None
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._event_start: float | None = None
        self._event_peak: float = -120.0

    def start(self) -> AudioMonitor:
        if not self.cfg.enabled:
            return self
        if not str(self.url).lower().startswith("rtsp://"):
            self.error = "audio requires an RTSP source"
            log.warning("Audio skipped: %s", self.error)
            return self
        self._thread = threading.Thread(target=self._run, name="audio-monitor", daemon=True)
        self._thread.start()
        return self

    def _spawn(self) -> subprocess.Popen:
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-rtsp_transport",
            "tcp",
            "-i",
            self.url,
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(self.cfg.sample_rate),
            "-f",
            "s16le",
            "-",
        ]
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def _run(self) -> None:
        chunk_samples = int(self.cfg.sample_rate * self.cfg.frame_ms / 1000)
        chunk_bytes = chunk_samples * 2  # int16
        backoff = 2.0

        while not self._stop.is_set():
            try:
                self._proc = self._spawn()
            except FileNotFoundError:
                self.error = "ffmpeg not found in PATH"
                log.error("Audio: %s", self.error)
                return

            assert self._proc.stdout is not None
            got_any = False
            while not self._stop.is_set():
                raw = self._proc.stdout.read(chunk_bytes)
                if not raw or len(raw) < chunk_bytes:
                    break
                got_any = True
                self.available = True
                samples = np.frombuffer(raw, dtype=np.int16)
                self._process(dbfs(samples), time.time())

            self._teardown()
            if self._stop.is_set():
                break
            if not got_any:
                self.error = "no audio track in the stream (camera without a microphone?)"
                log.warning("Audio: %s", self.error)
                return
            log.warning("Audio stream interrupted - restarting in %.0fs", backoff)
            self._stop.wait(backoff)

    def _process(self, level: float, ts: float) -> None:
        self.level_dbfs = level
        self.peak_dbfs = max(self.peak_dbfs, level)

        if self._event_start is None:
            if level >= self.cfg.trigger_dbfs:
                self._event_start = ts
                self._event_peak = level
                self.loud = True
        else:
            self._event_peak = max(self._event_peak, level)
            if level < self.cfg.release_dbfs:
                duration = ts - self._event_start
                if duration >= self.cfg.min_event_s and self.on_event is not None:
                    self.on_event(
                        AudioEvent(
                            started_at=self._event_start, ended_at=ts, peak_dbfs=self._event_peak
                        )
                    )
                self._event_start = None
                self._event_peak = -120.0
                self.loud = False

    def _teardown(self) -> None:
        if self._proc is not None:
            for stream in (self._proc.stdout, self._proc.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:  # noqa: BLE001
                    pass
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None

    def stop(self) -> None:
        self._stop.set()
        self._teardown()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
