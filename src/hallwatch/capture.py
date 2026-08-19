"""Frame reading from RTSP / webcam / file, with automatic reconnect.

Network cameras produce a real-time stream: if the consumer is slower than the
camera, frames pile up in the buffer and the picture drifts away from reality. That
is why, for live sources, the reader thread always holds only the NEWEST frame and
discards old ones. For files (offline/dev mode) we read sequentially so that no
frames are lost.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)


def parse_source(source: str) -> int | str:
    """'0' -> 0 (webcam), anything else stays a string (RTSP/file)."""
    s = str(source).strip()
    return int(s) if s.isdigit() else s


def is_live_source(source: str) -> bool:
    s = str(source).strip()
    if s.isdigit():
        return True
    lowered = s.lower()
    return lowered.startswith(("rtsp://", "rtmp://", "http://", "https://"))


class FrameSource:
    """A uniform frame source for an RTSP camera, a webcam and a video file."""

    def __init__(
        self,
        source: str,
        width: int | None = None,
        fps_limit: float | None = None,
        reconnect_delay_s: float = 3.0,
    ) -> None:
        self.source = source
        self.width = width
        self.fps_limit = fps_limit
        self.reconnect_delay_s = reconnect_delay_s
        self.live = is_live_source(source)

        self._cap: cv2.VideoCapture | None = None
        self._frame: np.ndarray | None = None
        self._frame_ts: float = 0.0
        self._seq: int = 0
        self._last_served_seq: int = -1
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.reconnects = 0
        self.native_fps: float = 0.0
        self.finished = False  # True once the file has ended

    # -- opening --------------------------------------------------------------
    def _open_hint(self) -> str:
        """A human-oriented hint appended to open-failure messages."""
        s = str(self.source).strip()
        if s.isdigit() and sys.platform == "darwin":
            return (
                " (grant Camera permission to your terminal in "
                "System Settings > Privacy & Security)"
            )
        if s.lower().startswith("rtsp://"):
            return f" (verify the URL with: hallwatch probe --source {s})"
        return ""

    def _open(self) -> bool:
        target = parse_source(self.source)
        if (
            isinstance(target, str)
            and not target.lower().startswith(("rtsp://", "rtmp://", "http://", "https://"))
            and not Path(target).exists()
        ):
            raise FileNotFoundError(f"No such video file: {target}")

        cap = cv2.VideoCapture(target, cv2.CAP_FFMPEG if isinstance(target, str) else cv2.CAP_ANY)
        if not cap.isOpened():
            cap.release()
            return False

        if self.live:
            # small buffer = low latency
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.native_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        self._cap = cap
        return True

    def open(self) -> None:
        """Opens the source (blocking, with retry for live sources)."""
        while not self._stop.is_set():
            try:
                if self._open():
                    log.info(
                        "Source opened: %s (live=%s, fps=%.1f)",
                        self.source,
                        self.live,
                        self.native_fps,
                    )
                    return
            except FileNotFoundError:
                raise
            if not self.live:
                raise RuntimeError(f"Failed to open source: {self.source}{self._open_hint()}")
            log.warning(
                "Failed to open %s, retrying in %.1fs%s",
                self.source,
                self.reconnect_delay_s,
                self._open_hint(),
            )
            self._stop.wait(self.reconnect_delay_s)

    # -- thread for live sources --------------------------------------------
    def start(self) -> FrameSource:
        self.open()
        if self.live:
            self._thread = threading.Thread(target=self._pump, name="frame-pump", daemon=True)
            self._thread.start()
        return self

    def _pump(self) -> None:
        assert self._cap is not None
        fails = 0
        while not self._stop.is_set():
            ok, frame = self._cap.read()
            if not ok or frame is None:
                fails += 1
                if fails >= 15:
                    log.warning("Stream died - reconnecting")
                    self._reconnect()
                    fails = 0
                else:
                    self._stop.wait(0.05)
                continue
            fails = 0
            frame = self._resize(frame)
            with self._lock:
                self._frame = frame
                self._frame_ts = time.time()
                self._seq += 1

    def _reconnect(self) -> None:
        self.reconnects += 1
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self._stop.wait(self.reconnect_delay_s)
        try:
            self.open()
        except Exception as exc:  # noqa: BLE001 - the loop has to survive
            log.error("Reconnect failed: %s", exc)

    # -- read ---------------------------------------------------------------
    def _resize(self, frame: np.ndarray) -> np.ndarray:
        if not self.width:
            return frame
        # libx264 + yuv420p require EVEN dimensions; an odd height (portrait phone
        # cameras) would make ffmpeg die and every clip get deleted as empty.
        width = max(2, self.width - self.width % 2)
        if frame.shape[1] == width:
            return frame
        h = int(round(frame.shape[0] * width / frame.shape[1]))
        h = max(2, h - h % 2)  # round DOWN to even
        return cv2.resize(frame, (width, h), interpolation=cv2.INTER_AREA)

    def read(self, timeout: float = 5.0) -> tuple[np.ndarray | None, float]:
        """Returns (frame, timestamp). A None frame means no new data or end of file."""
        if not self.live:
            assert self._cap is not None
            ok, frame = self._cap.read()
            if not ok or frame is None:
                self.finished = True
                return None, time.time()
            if self.fps_limit and self.native_fps > self.fps_limit:
                # when replaying a file we thin frames down to the target fps
                skip = int(self.native_fps / self.fps_limit) - 1
                for _ in range(max(0, skip)):
                    self._cap.grab()
            return self._resize(frame), time.time()

        deadline = time.time() + timeout
        while time.time() < deadline and not self._stop.is_set():
            with self._lock:
                if self._frame is not None and self._seq != self._last_served_seq:
                    self._last_served_seq = self._seq
                    return self._frame.copy(), self._frame_ts
            time.sleep(0.005)
        return None, time.time()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def __enter__(self) -> FrameSource:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()
