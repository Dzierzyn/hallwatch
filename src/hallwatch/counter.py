"""Liczenie osob: przekroczenia linii + obecnosc w strefach.

Algorytm linii: dla kazdego tracka trzymamy po ktorej stronie odcinka byl
poprzednio (znak iloczynu wektorowego). Zmiana znaku = przekroczenie. Dodatkowo
sprawdzamy, czy punkt przeciecia lezy W OBREBIE odcinka - inaczej osoba idaca
daleko obok liczylaby sie jako przejscie (bo prosta jest nieskonczona).
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

import cv2
import numpy as np

from .config import CountingCfg
from .detect import Detection
from .privacy import denormalize


@dataclass
class Crossing:
    track_id: int
    ts: float
    direction: str  # etykieta z configu, np. "wejscie"
    sign: int  # +1 / -1


@dataclass
class CountState:
    positive: int = 0
    negative: int = 0
    zone_counts: dict[str, int] = field(default_factory=dict)
    active_tracks: int = 0
    unique_seen: int = 0


class PeopleCounter:
    def __init__(self, cfg: CountingCfg, history_len: int = 64) -> None:
        self.cfg = cfg
        self.state = CountState()
        self._sides: dict[int, int] = {}
        self._last_seen: dict[int, float] = {}
        self._unique: set[int] = set()
        self.trails: dict[int, deque[tuple[int, int]]] = defaultdict(
            lambda: deque(maxlen=history_len)
        )
        self._zone_cache: dict[tuple[int, int], list[tuple[str, np.ndarray]]] = {}
        self._line_cache: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}

    # -- geometria ----------------------------------------------------------
    def line_px(self, shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray] | None:
        if self.cfg.line is None:
            return None
        key = (shape[0], shape[1])
        if key not in self._line_cache:
            pts = denormalize(list(self.cfg.line), shape).astype(np.float64)
            self._line_cache[key] = (pts[0], pts[1])
        return self._line_cache[key]

    def zones_px(self, shape: tuple[int, int]) -> list[tuple[str, np.ndarray]]:
        key = (shape[0], shape[1])
        if key not in self._zone_cache:
            self._zone_cache[key] = [
                (z.name, denormalize(z.polygon, shape)) for z in self.cfg.zones
            ]
        return self._zone_cache[key]

    @staticmethod
    def _side(a: np.ndarray, b: np.ndarray, p: tuple[float, float]) -> float:
        return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])

    @staticmethod
    def _within_segment(a: np.ndarray, b: np.ndarray, p: tuple[float, float]) -> bool:
        ab = b - a
        denom = float(ab[0] ** 2 + ab[1] ** 2)
        if denom == 0.0:
            return False
        t = ((p[0] - a[0]) * ab[0] + (p[1] - a[1]) * ab[1]) / denom
        return -0.05 <= t <= 1.05

    # -- aktualizacja -------------------------------------------------------
    def update(
        self, detections: list[Detection], shape: tuple[int, int], ts: float | None = None
    ) -> list[Crossing]:
        ts = ts if ts is not None else time.time()
        crossings: list[Crossing] = []
        line = self.line_px(shape)
        zones = self.zones_px(shape)
        zone_counts = {name: 0 for name, _ in zones}

        for det in detections:
            if det.track_id is None:
                continue
            tid = det.track_id
            anchor = det.anchor(self.cfg.anchor)
            self._last_seen[tid] = ts
            self._unique.add(tid)
            self.trails[tid].append((int(anchor[0]), int(anchor[1])))

            for name, poly in zones:
                if len(poly) >= 3 and cv2.pointPolygonTest(poly, (float(anchor[0]), float(anchor[1])), False) >= 0:
                    zone_counts[name] += 1

            if line is None:
                continue
            a, b = line
            raw = self._side(a, b, anchor)
            side = 0 if abs(raw) < 1e-9 else (1 if raw > 0 else -1)
            prev = self._sides.get(tid)
            if prev is not None and side != 0 and prev != 0 and side != prev:
                if self._within_segment(a, b, anchor):
                    label = (
                        self.cfg.direction_labels.positive
                        if side > 0
                        else self.cfg.direction_labels.negative
                    )
                    if side > 0:
                        self.state.positive += 1
                    else:
                        self.state.negative += 1
                    crossings.append(Crossing(track_id=tid, ts=ts, direction=label, sign=side))
            if side != 0:
                self._sides[tid] = side

        self.state.zone_counts = zone_counts
        self.state.active_tracks = sum(1 for d in detections if d.track_id is not None)
        self.state.unique_seen = len(self._unique)
        self._gc(ts)
        return crossings

    def _gc(self, ts: float, max_idle_s: float = 30.0) -> None:
        """Zwalnia pamiec po trackach, ktorych dawno nie widzielismy."""
        stale = [tid for tid, seen in self._last_seen.items() if ts - seen > max_idle_s]
        for tid in stale:
            self._last_seen.pop(tid, None)
            self._sides.pop(tid, None)
            self.trails.pop(tid, None)
