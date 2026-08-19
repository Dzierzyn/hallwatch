"""Privacy masks, applied to the frame BEFORE recording, streaming and upload.

This is not decoration: areas that do not belong to the system's owner should never
reach disk or the cloud. The mask is applied at the source of the pipeline, so every
downstream consumer already receives a masked frame.
"""

from __future__ import annotations

import cv2
import numpy as np

from .config import MaskCfg


def denormalize(polygon: list[tuple[float, float]], shape: tuple[int, int]) -> np.ndarray:
    """Coordinates 0..1 -> pixels. shape = (h, w)."""
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
        """Returns the frame with masks applied (in place, on a copy of the input)."""
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
                k = int(cfg.strength) | 1  # the kernel has to be odd
                blurred = cv2.GaussianBlur(roi, (k, k), 0)

            sub_mask = mask[y : y + h, x : x + w] > 0
            roi[sub_mask] = blurred[sub_mask]
        return out

    def contains(self, point: tuple[float, float], shape: tuple[int, int]) -> bool:
        """Whether the point (px) lies inside any mask - used to discard detections."""
        for _cfg, pts in self._prepared(shape):
            if (
                len(pts) >= 3
                and cv2.pointPolygonTest(pts, (float(point[0]), float(point[1])), False) >= 0
            ):
                return True
        return False
