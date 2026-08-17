"""Rysowanie nakladki: boxy, sledzone trajektorie, linia zliczajaca, strefy, HUD.

Uwaga: cv2.putText obsluguje tylko ASCII (fonty Hershey), dlatego wszystkie
etykiety w overlayu sa bez polskich znakow. Interfejs webowy nie ma tego
ograniczenia.
"""

from __future__ import annotations

import cv2
import numpy as np

from .counter import PeopleCounter
from .detect import Detection

GREEN = (80, 220, 120)
BLUE = (255, 190, 80)
RED = (70, 70, 235)
YELLOW = (60, 220, 250)
WHITE = (245, 245, 245)
GREY = (150, 150, 150)
FONT = cv2.FONT_HERSHEY_SIMPLEX


def _color_for_id(track_id: int | None) -> tuple[int, int, int]:
    if track_id is None:
        return GREY
    # deterministyczny, dobrze rozrozniajacy sie kolor per track
    h = (track_id * 47) % 180
    hsv = np.uint8([[[h, 200, 255]]])
    bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0][0]
    return int(bgr[0]), int(bgr[1]), int(bgr[2])


def draw_zones(frame: np.ndarray, counter: PeopleCounter) -> None:
    for name, poly in counter.zones_px(frame.shape):
        if len(poly) < 3:
            continue
        overlay = frame.copy()
        cv2.fillPoly(overlay, [poly], (60, 120, 60))
        cv2.addWeighted(overlay, 0.18, frame, 0.82, 0, dst=frame)
        cv2.polylines(frame, [poly], True, GREEN, 1, cv2.LINE_AA)
        count = counter.state.zone_counts.get(name, 0)
        x, y = int(poly[:, 0].min()), int(poly[:, 1].min())
        cv2.putText(frame, f"{name}: {count}", (x + 6, y + 18), FONT, 0.45, GREEN, 1, cv2.LINE_AA)


def draw_line(frame: np.ndarray, counter: PeopleCounter) -> None:
    line = counter.line_px(frame.shape)
    if line is None:
        return
    a, b = line
    p1 = (int(a[0]), int(a[1]))
    p2 = (int(b[0]), int(b[1]))
    cv2.line(frame, p1, p2, YELLOW, 2, cv2.LINE_AA)
    for p in (p1, p2):
        cv2.circle(frame, p, 4, YELLOW, -1, cv2.LINE_AA)
    labels = counter.cfg.direction_labels
    text = f"{labels.positive}: {counter.state.positive}  {labels.negative}: {counter.state.negative}"
    cv2.putText(frame, text, (p1[0], max(16, p1[1] - 10)), FONT, 0.5, YELLOW, 1, cv2.LINE_AA)


def draw_trails(frame: np.ndarray, counter: PeopleCounter) -> None:
    for tid, trail in counter.trails.items():
        if len(trail) < 2:
            continue
        pts = np.array(trail, dtype=np.int32)
        cv2.polylines(frame, [pts], False, _color_for_id(tid), 2, cv2.LINE_AA)


def draw_detections(
    frame: np.ndarray,
    detections: list[Detection],
    names: dict[int, str],
    anchor_mode: str = "feet",
) -> None:
    for det in detections:
        x1, y1, x2, y2 = (int(v) for v in det.xyxy)
        color = _color_for_id(det.track_id)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2, cv2.LINE_AA)
        label = names.get(det.cls, str(det.cls))
        tag = f"#{det.track_id} {label} {det.conf:.2f}" if det.track_id else f"{label} {det.conf:.2f}"
        (tw, th), _ = cv2.getTextSize(tag, FONT, 0.45, 1)
        cv2.rectangle(frame, (x1, max(0, y1 - th - 8)), (x1 + tw + 8, y1), color, -1)
        cv2.putText(frame, tag, (x1 + 4, max(11, y1 - 5)), FONT, 0.45, (20, 20, 20), 1, cv2.LINE_AA)
        ax, ay = det.anchor(anchor_mode)
        cv2.circle(frame, (int(ax), int(ay)), 3, color, -1, cv2.LINE_AA)


def draw_motion(frame: np.ndarray, boxes: list[tuple[int, int, int, int]]) -> None:
    for x, y, w, h in boxes:
        cv2.rectangle(frame, (x, y), (x + w, y + h), (90, 90, 90), 1, cv2.LINE_AA)


def draw_hud(frame: np.ndarray, lines: list[tuple[str, tuple[int, int, int]]]) -> None:
    if not lines:
        return
    pad, lh = 10, 20
    box_h = pad * 2 + lh * len(lines)
    box_w = 8 + max(cv2.getTextSize(t, FONT, 0.5, 1)[0][0] for t, _ in lines) + pad * 2
    overlay = frame.copy()
    cv2.rectangle(overlay, (8, 8), (8 + box_w, 8 + box_h), (25, 25, 25), -1)
    cv2.addWeighted(overlay, 0.62, frame, 0.38, 0, dst=frame)
    for i, (text, color) in enumerate(lines):
        cv2.putText(
            frame, text, (16 + pad, 8 + pad + lh * i + 14), FONT, 0.5, color, 1, cv2.LINE_AA
        )


def draw_rec_indicator(frame: np.ndarray) -> None:
    h, w = frame.shape[:2]
    cv2.circle(frame, (w - 30, 26), 8, RED, -1, cv2.LINE_AA)
    cv2.putText(frame, "REC", (w - 78, 32), FONT, 0.55, RED, 2, cv2.LINE_AA)


def draw_timestamp(frame: np.ndarray, text: str) -> None:
    h, w = frame.shape[:2]
    (tw, th), _ = cv2.getTextSize(text, FONT, 0.5, 1)
    cv2.putText(frame, text, (w - tw - 12, h - 12), FONT, 0.5, (30, 30, 30), 3, cv2.LINE_AA)
    cv2.putText(frame, text, (w - tw - 12, h - 12), FONT, 0.5, WHITE, 1, cv2.LINE_AA)
