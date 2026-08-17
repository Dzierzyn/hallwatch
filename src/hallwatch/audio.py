"""Detekcja dzwieku ze strumienia RTSP (osobny proces ffmpeg -> surowe PCM).

Dlaczego osobno od wideo: OpenCV w ogole nie wystawia audio z RTSP. Bierzemy
wiec ten sam URL, mowimy ffmpegowi "-vn" (bez wideo) i dostajemy strumien
16-bitowych probek, ktory liczymy w numpy.

Poziom liczymy w dBFS (0 dB = maksymalna amplituda, wartosci sa ujemne), z
histereza: inny prog na WLACZENIE alarmu i inny na jego wygaszenie. Bez tego
dzwiek oscylujacy wokol progu generowalby setki zdarzen na sekunde.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable

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
    """Watek nasluchujacy poziomu dzwieku; wola callback po zakonczeniu zdarzenia."""

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

    def start(self) -> "AudioMonitor":
        if not self.cfg.enabled:
            return self
        if not str(self.url).lower().startswith("rtsp://"):
            self.error = "audio wymaga zrodla RTSP"
            log.warning("Audio pominiete: %s", self.error)
            return self
        self._thread = threading.Thread(target=self._run, name="audio-monitor", daemon=True)
        self._thread.start()
        return self

    def _spawn(self) -> subprocess.Popen:
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-i", self.url,
            "-vn", "-ac", "1", "-ar", str(self.cfg.sample_rate),
            "-f", "s16le", "-",
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
                self.error = "brak ffmpeg w PATH"
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
                self.error = "brak sciezki audio w strumieniu (kamera bez mikrofonu?)"
                log.warning("Audio: %s", self.error)
                return
            log.warning("Strumien audio przerwany - restart za %.0fs", backoff)
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
