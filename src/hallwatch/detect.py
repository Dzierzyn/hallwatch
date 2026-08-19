"""Person detection and tracking (YOLO11 + ByteTrack from Ultralytics).

The detector says "where the person is in this frame". The tracker attaches
IDENTITY over time (track_id), and that is what makes counting possible: without
tracking, the same person standing for 10 seconds in front of the door would be
counted 150 times.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from .config import DetectionCfg

log = logging.getLogger(__name__)


@dataclass
class Detection:
    track_id: int | None
    cls: int
    conf: float
    xyxy: tuple[float, float, float, float]

    @property
    def feet(self) -> tuple[float, float]:
        """Srodek dolnej krawedzi boxa."""
        x1, _y1, x2, y2 = self.xyxy
        return ((x1 + x2) / 2.0, y2)

    @property
    def center(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.xyxy
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)

    def anchor(self, mode: str = "feet") -> tuple[float, float]:
        """The point on a track used for counting crossings and zone presence.

        'feet' (camera viewing from the side or at a downward angle along the
        corridor): the feet stay on the floor, so they do not jump when a person
        raises their arms or partially leaves the frame, and it is the floor that
        crosses the counting line.

        'center' (ceiling camera looking straight down): from above there are no
        "feet at the bottom of the box", the silhouette is a blob whose lower edge
        shifts randomly with every step. The box centre is then the only stable point.
        """
        return self.center if mode == "center" else self.feet


def resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except Exception:  # noqa: BLE001
        pass
    return "cpu"


class PersonDetector:
    def __init__(self, cfg: DetectionCfg) -> None:
        from ultralytics import YOLO  # import leniwy - ciagnie torch (~kilka sekund)

        self.cfg = cfg
        self.device = resolve_device(cfg.device)
        log.info("Loading model %s on device %s", cfg.model, self.device)
        self.model = YOLO(cfg.model)
        self.names: dict[int, str] = dict(self.model.names or {})

    def detect(self, frame: np.ndarray, track: bool = True) -> list[Detection]:
        if track:
            results = self.model.track(
                frame,
                persist=True,
                tracker=self.cfg.tracker,
                classes=self.cfg.classes,
                conf=self.cfg.conf,
                iou=self.cfg.iou,
                imgsz=self.cfg.imgsz,
                device=self.device,
                verbose=False,
            )
        else:
            results = self.model.predict(
                frame,
                classes=self.cfg.classes,
                conf=self.cfg.conf,
                iou=self.cfg.iou,
                imgsz=self.cfg.imgsz,
                device=self.device,
                verbose=False,
            )

        out: list[Detection] = []
        if not results:
            return out
        boxes = results[0].boxes
        if boxes is None or boxes.xyxy is None or len(boxes) == 0:
            return out

        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy() if boxes.conf is not None else np.ones(len(xyxy))
        clss = boxes.cls.cpu().numpy() if boxes.cls is not None else np.zeros(len(xyxy))
        ids = boxes.id.cpu().numpy() if getattr(boxes, "id", None) is not None else None

        for i in range(len(xyxy)):
            out.append(
                Detection(
                    track_id=int(ids[i]) if ids is not None else None,
                    cls=int(clss[i]),
                    conf=float(confs[i]),
                    xyxy=tuple(float(v) for v in xyxy[i]),  # type: ignore[arg-type]
                )
            )
        return out
