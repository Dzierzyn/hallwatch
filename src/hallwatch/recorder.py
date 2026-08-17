"""Nagrywanie zdarzen z pre-rollem (bufor pierscieniowy) do H.264 przez ffmpeg.

Kluczowa cecha: gdy pipeline stwierdzi zdarzenie, moment jego POCZATKU jest juz
przeszlosci. Dlatego kazda klatka ladauje najpierw w buforze pierscieniowym o
dlugosci pre_roll_s - i gdy zapada decyzja o nagraniu, bufor jest wylewany do
pliku. Efekt: na klipie widac, jak ktos wchodzi, a nie doklejke od polowy.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

from .config import RecordingCfg

log = logging.getLogger(__name__)


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


class ClipWriter:
    """Pipe surowych klatek BGR -> ffmpeg -> mp4 (H.264, playowalny w przegladarce)."""

    def __init__(self, path: Path, width: int, height: int, fps: float, crf: int = 26) -> None:
        self.path = path
        self.width = width
        self.height = height
        self.fps = max(1.0, float(fps))
        self.frame_interval = 1.0 / self.fps
        self.frames_written = 0
        self.start_ts: float | None = None
        path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{width}x{height}", "-r", f"{self.fps:g}",
            "-i", "-",
            "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
            "-crf", str(crf), "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(path),
        ]
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )

    def feed(self, frame: np.ndarray, ts: float) -> None:
        """Zapisuje klatke, powtarzajac ja jesli trzeba, by czas klipu = czas realny."""
        if self.proc.poll() is not None or self.proc.stdin is None:
            return
        if frame.shape[1] != self.width or frame.shape[0] != self.height:
            frame = cv2.resize(frame, (self.width, self.height), interpolation=cv2.INTER_AREA)
        if not frame.flags["C_CONTIGUOUS"]:
            frame = np.ascontiguousarray(frame)

        if self.start_ts is None:
            self.start_ts = ts
            needed = 1
        else:
            target = int((ts - self.start_ts) / self.frame_interval) + 1
            needed = min(max(target - self.frames_written, 1), 4)  # cap: nie puchnij przy zwiechach

        data = frame.tobytes()
        try:
            for _ in range(needed):
                self.proc.stdin.write(data)
                self.frames_written += 1
        except (BrokenPipeError, ValueError):
            log.warning("ffmpeg zamknal pipe dla %s", self.path.name)

    @property
    def duration_s(self) -> float:
        return self.frames_written / self.fps

    def close(self) -> Path | None:
        err = b""
        # communicate() samo domyka stdin i czeka, az ffmpeg dopisze moov atom
        try:
            _, err = self.proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            err = b"timeout"
        except (BrokenPipeError, ValueError):
            self.proc.kill()
            self.proc.wait(timeout=5)
        if self.frames_written == 0 or not self.path.exists() or self.path.stat().st_size == 0:
            log.warning("Klip pusty, usuwam: %s (%s)", self.path.name, err[:200] if err else "")
            self.path.unlink(missing_ok=True)
            return None
        return self.path


class EventRecorder:
    """Bufor pierscieniowy + zarzadzanie aktywnym klipem zdarzenia."""

    def __init__(self, cfg: RecordingCfg, out_dir: Path) -> None:
        self.cfg = cfg
        self.out_dir = out_dir
        buffer_len = max(1, int(cfg.pre_roll_s * cfg.fps))
        self._ring: deque[tuple[np.ndarray, float]] = deque(maxlen=buffer_len)
        self._writer: ClipWriter | None = None
        self._pending: tuple[float, bool] | None = None  # (ts, uzyj_pre_rolla)
        self._started_at: float = 0.0
        self.enabled = cfg.enabled and ffmpeg_available()
        if cfg.enabled and not self.enabled:
            log.error("Nagrywanie wylaczone: brak ffmpeg w PATH")

    @property
    def active(self) -> bool:
        return self._writer is not None or self._pending is not None

    def push(self, frame: np.ndarray, ts: float) -> None:
        """Wolane dla KAZDEJ klatki: karmi bufor pierscieniowy i aktywny klip."""
        if not self.enabled:
            return
        if self._writer is None:
            if self._pending is None:
                self._ring.append((frame, ts))
                return
            self._begin(frame)

        assert self._writer is not None
        self._writer.feed(frame, ts)
        if self._writer.duration_s >= self.cfg.max_clip_s:
            log.info("Klip osiagnal max_clip_s - domykam i zaczynam nastepny segment")
            self.stop()
            self._pending = (ts, False)  # nowy segment startuje od nastepnej klatki

    def start(self, ts: float, use_preroll: bool = True) -> None:
        """Zglasza chec nagrywania. Writer powstaje przy pierwszej klatce,
        bo dopiero ona zna wymiary kadru."""
        if not self.enabled or self._writer is not None or self._pending is not None:
            return
        self._pending = (ts, use_preroll)

    def _begin(self, frame: np.ndarray) -> None:
        assert self._pending is not None
        ts, use_preroll = self._pending
        self._pending = None
        frames = list(self._ring) if use_preroll else []
        h, w = frame.shape[:2]

        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(ts))
        path = self.out_dir / f"{stamp}.mp4"
        self._writer = ClipWriter(path, w, h, self.cfg.fps, self.cfg.crf)
        self._started_at = frames[0][1] if frames else ts
        for f, fts in frames:
            self._writer.feed(f, fts)
        self._ring.clear()
        log.info("Start nagrywania: %s (pre-roll %d klatek)", path.name, len(frames))

    def stop(self) -> Path | None:
        self._pending = None
        if self._writer is None:
            return None
        writer, self._writer = self._writer, None
        return writer.close()

    def snapshot(self, frame: np.ndarray, ts: float, suffix: str = "") -> Path | None:
        if not self.cfg.snapshot:
            return None
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(ts))
        path = self.out_dir / f"{stamp}{suffix}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        ok = cv2.imwrite(str(path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        return path if ok else None
