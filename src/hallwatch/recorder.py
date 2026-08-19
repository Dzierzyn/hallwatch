"""Event recording with pre-roll (a ring buffer) to H.264 through ffmpeg.

The key property: by the time the pipeline decides an event has happened, the moment
it BEGAN is already in the past. That is why every frame first lands in a ring buffer
of length pre_roll_s, and when the decision to record is made the buffer is flushed
to file. The effect: the clip shows someone walking in, rather than a fragment
starting halfway through.
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
    """Pipes raw BGR frames -> ffmpeg -> mp4 (H.264, playable in a browser)."""

    def __init__(
        self,
        path: Path,
        width: int,
        height: int,
        fps: float,
        crf: int = 26,
        codec: str = "libx264",
    ) -> None:
        self.path = path
        # libx264 + yuv420p require EVEN dimensions - floor defensively; feed()
        # resizes any frame that does not match, so this is always safe.
        self.width = max(2, width - width % 2)
        self.height = max(2, height - height % 2)
        self.fps = max(1.0, float(fps))
        self.frame_interval = 1.0 / self.fps
        self.frames_written = 0
        self.start_ts: float | None = None
        path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            f"{self.width}x{self.height}",
            "-r",
            f"{self.fps:g}",
            "-i",
            "-",
            "-an",
            "-c:v",
            codec,
        ]
        if codec == "libx264":
            # hardware encoders do not accept these flags
            cmd += ["-preset", "veryfast", "-tune", "zerolatency"]
        cmd += [
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(path),
        ]
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )

    def feed(self, frame: np.ndarray, ts: float) -> None:
        """Writes a frame, repeating it if needed so that clip time matches real time."""
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
            needed = min(max(target - self.frames_written, 1), 4)  # cap: do not grow during stalls

        data = frame.tobytes()
        try:
            for _ in range(needed):
                self.proc.stdin.write(data)
                self.frames_written += 1
        except (BrokenPipeError, ValueError):
            log.warning("ffmpeg closed the pipe for %s", self.path.name)

    @property
    def duration_s(self) -> float:
        return self.frames_written / self.fps

    def close(self) -> Path | None:
        err = b""
        # communicate() closes stdin itself and waits for ffmpeg to append the moov atom
        try:
            _, err = self.proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            err = b"timeout"
        except (BrokenPipeError, ValueError):
            self.proc.kill()
            self.proc.wait(timeout=5)
        if self.frames_written == 0 or not self.path.exists() or self.path.stat().st_size == 0:
            log.error(
                "Empty clip, removing: %s%s",
                self.path.name,
                f" - ffmpeg stderr: {err.decode(errors='replace').strip()}" if err else "",
            )
            self.path.unlink(missing_ok=True)
            return None
        return self.path


class EventRecorder:
    """Ring buffer plus management of the active event clip."""

    def __init__(self, cfg: RecordingCfg, out_dir: Path) -> None:
        self.cfg = cfg
        self.out_dir = out_dir
        buffer_len = max(1, int(cfg.pre_roll_s * cfg.fps))
        self._ring: deque[tuple[np.ndarray, float]] = deque(maxlen=buffer_len)
        self._writer: ClipWriter | None = None
        self._pending: tuple[float, bool] | None = None  # (ts, use_preroll)
        self._started_at: float = 0.0
        self.enabled = cfg.enabled and ffmpeg_available()
        if cfg.enabled and not self.enabled:
            log.error("Recording disabled: ffmpeg not found in PATH")

    @property
    def active(self) -> bool:
        return self._writer is not None or self._pending is not None

    def push(self, frame: np.ndarray, ts: float) -> None:
        """Called for EVERY frame: feeds the ring buffer and the active clip."""
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
            log.info("Clip reached max_clip_s - closing it and starting the next segment")
            self.stop()
            self._pending = (ts, False)  # a new segment starts from the next frame

    def start(self, ts: float, use_preroll: bool = True) -> None:
        """Requests recording. The writer is created on the first frame,
        because only then are the frame dimensions known."""
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
        self._writer = ClipWriter(path, w, h, self.cfg.fps, self.cfg.crf, self.cfg.codec)
        self._started_at = frames[0][1] if frames else ts
        for f, fts in frames:
            self._writer.feed(f, fts)
        self._ring.clear()
        log.info("Recording started: %s (pre-roll %d frames)", path.name, len(frames))

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
