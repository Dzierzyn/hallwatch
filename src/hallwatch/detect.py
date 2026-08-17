"""Detekcja + tracking osob (YOLO11 + ByteTrack z Ultralytics).

Detektor mowi "gdzie jest osoba na tej klatce". Tracker dokleja do tego
TOZSAMOSC w czasie (track_id), i to on umozliwia liczenie: bez trackingu ta sama
osoba stojaca 10 sekund przed drzwiami zostalaby zliczona 150 razy.
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
        """Punkt tracka uzywany do liczenia przejsc i obecnosci w strefach.

        'feet' (kamera patrzaca z boku / skosem w dol korytarza): stopy trzymaja
        sie podlogi, wiec nie skacza, gdy osoba podnosi rece albo czesciowo
        wychodzi z kadru - a to podloga przecina linie zliczajaca.

        'center' (kamera pod sufitem patrzaca prosto w dol): z gory nie ma
        "stop na dole boxa" - sylwetka to plama, ktorej dolna krawedz zmienia
        sie losowo z kazdym krokiem. Srodek boxa jest wtedy jedynym stabilnym
        punktem.
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
        log.info("Laduje model %s na urzadzeniu %s", cfg.model, self.device)
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
