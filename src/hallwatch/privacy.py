"""Maski prywatnosci - nakladane na klatke PRZED nagraniem/streamem/uploadem.

To nie jest ozdoba: obszary, ktore nie naleza do wlasciciela systemu
powinny nigdy nie trafic na dysk ani do chmury. Maska aplikowana jest u zrodla
pipeline'u, wiec kazdy dalszy konsument dostaje juz zaslonieta klatke.
"""

from __future__ import annotations

import cv2
import numpy as np

from .config import MaskCfg


def denormalize(polygon: list[tuple[float, float]], shape: tuple[int, int]) -> np.ndarray:
    """Wspolrzedne 0..1 -> piksele. shape = (h, w)."""
    h, w = shape[:2]
    pts = np.array([[p[0] * w, p[1] * h] for p in polygon], dtype=np.float32)
    return np.round(pts).astype(np.int32)


class PrivacyMasker:
    def __init__(self, masks: list[MaskCfg]) -> None:
        self.masks = masks
        self._cache: dict[tuple[int, int], list[tuple[MaskCfg, np.ndarray]]] = {}

    @property
    def active(self) -> bool:
        return bool(self.masks)

    def _prepared(self, shape: tuple[int, int]) -> list[tuple[MaskCfg, np.ndarray]]:
        key = (shape[0], shape[1])
        if key not in self._cache:
            self._cache[key] = [(m, denormalize(m.polygon, shape)) for m in self.masks]
        return self._cache[key]

    def apply(self, frame: np.ndarray) -> np.ndarray:
        """Zwraca klatke z nalozonymi maskami (in-place na kopii wejscia)."""
        if not self.masks:
            return frame
        out = frame
        for cfg, pts in self._prepared(frame.shape):
            if len(pts) < 3:
                continue
            mask = np.zeros(frame.shape[:2], dtype=np.uint8)
            cv2.fillPoly(mask, [pts], 255)

            if cfg.mode == "black":
                out[mask > 0] = 0
                continue

            x, y, w, h = cv2.boundingRect(pts)
            x, y = max(0, x), max(0, y)
            w, h = min(w, frame.shape[1] - x), min(h, frame.shape[0] - y)
            if w <= 0 or h <= 0:
                continue
            roi = out[y : y + h, x : x + w]

            if cfg.mode == "pixelate":
                block = max(2, int(cfg.strength))
                small = cv2.resize(
                    roi,
                    (max(1, w // block), max(1, h // block)),
                    interpolation=cv2.INTER_LINEAR,
                )
                blurred = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
            else:  # blur
                k = int(cfg.strength) | 1  # kernel musi byc nieparzysty
                blurred = cv2.GaussianBlur(roi, (k, k), 0)

            sub_mask = mask[y : y + h, x : x + w] > 0
            roi[sub_mask] = blurred[sub_mask]
        return out

    def contains(self, point: tuple[float, float], shape: tuple[int, int]) -> bool:
        """Czy punkt (px) lezy w jakiejkolwiek masce - do odrzucania detekcji."""
        for _cfg, pts in self._prepared(shape):
            if len(pts) >= 3 and cv2.pointPolygonTest(pts, (float(point[0]), float(point[1])), False) >= 0:
                return True
        return False
