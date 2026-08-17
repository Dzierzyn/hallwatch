"""Detekcja ruchu przez odejmowanie tla (MOG2).

Rola w pipeline: TANI STRAZNIK. Sieć YOLO na kazdej klatce 24/7 to marnowanie
prądu i CPU, bo korytarz jest pusty wiekszosc czasu. MOG2 kosztuje ~1 ms na
klatke i decyduje, kiedy warto wybudzic detektor.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .config import MotionCfg


@dataclass
class MotionResult:
    detected: bool
    area_frac: float
    boxes: list[tuple[int, int, int, int]] = field(default_factory=list)
    warming_up: bool = False


class MotionDetector:
    def __init__(self, cfg: MotionCfg) -> None:
        self.cfg = cfg
        self._bg = cv2.createBackgroundSubtractorMOG2(
            history=cfg.history,
            varThreshold=cfg.var_threshold,
            detectShadows=False,
        )
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self._frames = 0

    def update(self, frame: np.ndarray) -> MotionResult:
        if not self.cfg.enabled:
            return MotionResult(detected=True, area_frac=1.0)

        self._frames += 1
        small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        blurred = cv2.GaussianBlur(small, (5, 5), 0)
        fg = self._bg.apply(blurred)

        if self._frames <= self.cfg.warmup_frames:
            return MotionResult(detected=False, area_frac=0.0, warming_up=True)

        _, fg = cv2.threshold(fg, 200, 255, cv2.THRESH_BINARY)
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, self._kernel)
        fg = cv2.dilate(fg, self._kernel, iterations=self.cfg.dilate_iter)

        contours, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        total = float(small.shape[0] * small.shape[1])
        min_area = self.cfg.min_area_frac * total

        boxes: list[tuple[int, int, int, int]] = []
        moved = 0.0
        scale_x = frame.shape[1] / small.shape[1]
        scale_y = frame.shape[0] / small.shape[0]
        for c in contours:
            area = cv2.contourArea(c)
            if area < min_area:
                continue
            moved += area
            x, y, w, h = cv2.boundingRect(c)
            boxes.append(
                (
                    int(x * scale_x),
                    int(y * scale_y),
                    int(w * scale_x),
                    int(h * scale_y),
                )
            )

        return MotionResult(detected=bool(boxes), area_frac=moved / total, boxes=boxes)
