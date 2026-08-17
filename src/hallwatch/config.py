"""Walidowana konfiguracja ladowana z config.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

Point = tuple[float, float]


class CameraCfg(BaseModel):
    source: str = "0"
    name: str = "korytarz"
    width: int | None = 960
    fps_limit: float | None = 15.0
    reconnect_delay_s: float = 3.0

    # continuous - strumien otwarty caly czas (kamera z zasilaniem)
    # on_demand  - laczymy sie dopiero na sygnal wybudzenia (kamera na baterii)
    mode: Literal["continuous", "on_demand"] = "continuous"
    session_seconds: float = 90.0  # twardy limit dlugosci jednej sesji
    session_idle_s: float = 20.0  # cisza, po ktorej rozlaczamy sie i wracamy do snu
    active_hours: str | None = None  # np. "07:00-23:00"; null = bez ograniczen


class DetectionCfg(BaseModel):
    model: str = "yolo11n.pt"
    device: str = "auto"
    conf: float = 0.40
    iou: float = 0.50
    classes: list[int] = Field(default_factory=lambda: [0])
    imgsz: int = 640
    tracker: str = "bytetrack.yaml"
    awake_seconds: float = 8.0


class MotionCfg(BaseModel):
    enabled: bool = True
    min_area_frac: float = 0.0015
    history: int = 400
    var_threshold: float = 24.0
    dilate_iter: int = 2
    warmup_frames: int = 30


class ZoneCfg(BaseModel):
    name: str
    polygon: list[Point]


class DirectionLabels(BaseModel):
    positive: str = "wejscie"
    negative: str = "wyjscie"


class CountingCfg(BaseModel):
    line: tuple[Point, Point] | None = None
    direction_labels: DirectionLabels = Field(default_factory=DirectionLabels)
    zones: list[ZoneCfg] = Field(default_factory=list)
    # punkt tracka decydujacy o przekroczeniu linii i obecnosci w strefie
    anchor: Literal["feet", "center"] = "feet"


class MaskCfg(BaseModel):
    name: str = "mask"
    polygon: list[Point]
    mode: Literal["blur", "black", "pixelate"] = "blur"
    strength: int = 35


class PrivacyCfg(BaseModel):
    masks: list[MaskCfg] = Field(default_factory=list)


class RecordingCfg(BaseModel):
    enabled: bool = True
    dir: str = "data/clips"
    pre_roll_s: float = 4.0
    post_roll_s: float = 6.0
    max_clip_s: float = 120.0
    fps: float = 12.0
    crf: int = 26
    snapshot: bool = True
    burn_overlay: bool = False
    retention_days: int = 14


class AudioCfg(BaseModel):
    enabled: bool = False
    sample_rate: int = 16000
    frame_ms: int = 100
    trigger_dbfs: float = -34.0
    release_dbfs: float = -40.0
    min_event_s: float = 0.4


class StorageCfg(BaseModel):
    db: str = "data/hallwatch.db"


class CloudCfg(BaseModel):
    enabled: bool = False
    endpoint_url: str = ""
    bucket: str = ""
    prefix: str = "hallwatch"
    region: str = "auto"
    delete_local_after_upload: bool = False


class NotifyCfg(BaseModel):
    enabled: bool = False
    ntfy_server: str = "https://ntfy.sh"
    ntfy_topic: str = ""
    min_interval_s: float = 60.0
    attach_snapshot: bool = True


class WebCfg(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    stream_fps: float = 10.0
    stream_quality: int = 70


class Config(BaseModel):
    camera: CameraCfg = Field(default_factory=CameraCfg)
    detection: DetectionCfg = Field(default_factory=DetectionCfg)
    motion: MotionCfg = Field(default_factory=MotionCfg)
    counting: CountingCfg = Field(default_factory=CountingCfg)
    privacy: PrivacyCfg = Field(default_factory=PrivacyCfg)
    recording: RecordingCfg = Field(default_factory=RecordingCfg)
    audio: AudioCfg = Field(default_factory=AudioCfg)
    storage: StorageCfg = Field(default_factory=StorageCfg)
    cloud: CloudCfg = Field(default_factory=CloudCfg)
    notify: NotifyCfg = Field(default_factory=NotifyCfg)
    web: WebCfg = Field(default_factory=WebCfg)

    # katalog, wzgledem ktorego rozwiazywane sa sciezki relatywne
    root: Path = Field(default_factory=Path.cwd, exclude=True)

    def path(self, relative: str) -> Path:
        p = Path(relative)
        return p if p.is_absolute() else self.root / p

    @classmethod
    def load(cls, path: str | Path = "config.yaml") -> "Config":
        path = Path(path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Brak pliku konfiguracji: {path}")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        cfg = cls.model_validate(data)
        cfg.root = path.parent
        return cfg
