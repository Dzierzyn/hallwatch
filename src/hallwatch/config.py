"""Validated configuration loaded from config.yaml.

Layout: the `defaults` section describes shared behaviour, and `cameras` lists the
cameras, each of which may override any fragment. That way the corridor and the
street differ only in what genuinely differs (object classes, operating mode,
recording), rather than through a full copy of the settings.

An old single-camera config still works: it is loaded and wrapped into a
one-element list, so upgrading does not require rewriting the file.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

Point = tuple[float, float]


# --- building blocks of a camera profile -------------------------------------
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
    positive: str = "in"
    negative: str = "out"


class CountingCfg(BaseModel):
    line: tuple[Point, Point] | None = None
    direction_labels: DirectionLabels = Field(default_factory=DirectionLabels)
    zones: list[ZoneCfg] = Field(default_factory=list)
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
    codec: str = "libx264"
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


class SamplingCfg(BaseModel):
    """Sampling windows for traffic statistics.

    Counting cars off a PIR trigger would produce garbage: it would miss most
    vehicles, and there would be no way to know which ones. Instead we observe a
    short window at a fixed interval and extrapolate. That is a sample, not a
    census, and it is described as such in the data (a sampled column plus a
    coefficient).
    """

    every_minutes: float = 60.0
    seconds: float = 300.0
    align_to_clock: bool = True  # start on the hour, not from process start

    @property
    def duty_cycle(self) -> float:
        window = max(1.0, self.every_minutes * 60.0)
        return min(1.0, self.seconds / window)


# --- camera profile ----------------------------------------------------------
class CameraProfile(BaseModel):
    name: str = "camera"
    source: str = "0"
    width: int | None = 960
    fps_limit: float | None = 15.0
    reconnect_delay_s: float = 3.0

    mode: Literal["continuous", "on_demand", "sampling"] = "continuous"
    session_seconds: float = 90.0
    session_idle_s: float = 20.0
    active_hours: str | None = None
    watch_hold_s: float = 25.0
    watch_max_s: float = 600.0
    sampling: SamplingCfg = Field(default_factory=SamplingCfg)

    detection: DetectionCfg = Field(default_factory=DetectionCfg)
    motion: MotionCfg = Field(default_factory=MotionCfg)
    counting: CountingCfg = Field(default_factory=CountingCfg)
    privacy: PrivacyCfg = Field(default_factory=PrivacyCfg)
    recording: RecordingCfg = Field(default_factory=RecordingCfg)
    audio: AudioCfg = Field(default_factory=AudioCfg)

    @property
    def slug(self) -> str:
        """A safe name for paths and URLs."""
        return "".join(c if c.isalnum() or c in "-_" else "-" for c in self.name.lower())

    @property
    def clip_dir(self) -> str:
        """Each camera has its own clip directory, otherwise names based on
        seconds would collide between cameras."""
        return f"{self.recording.dir.rstrip('/')}/{self.slug}"


# --- global settings ----------------------------------------------------------
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
    auth_token: str = ""  # empty = no auth (safe only on localhost)


LEGACY_KEYS = ("detection", "motion", "counting", "privacy", "recording", "audio")


def _deep_merge(base: dict, override: dict) -> dict:
    """Merges dictionaries recursively; values from override win."""
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class Config(BaseModel):
    cameras: list[CameraProfile] = Field(default_factory=lambda: [CameraProfile()])
    storage: StorageCfg = Field(default_factory=StorageCfg)
    cloud: CloudCfg = Field(default_factory=CloudCfg)
    notify: NotifyCfg = Field(default_factory=NotifyCfg)
    web: WebCfg = Field(default_factory=WebCfg)

    root: Path = Field(default_factory=Path.cwd, exclude=True)

    @model_validator(mode="after")
    def _unique_names(self) -> Config:
        seen: set[str] = set()
        for cam in self.cameras:
            if cam.slug in seen:
                raise ValueError(f"Duplicate camera name: {cam.name}")
            seen.add(cam.slug)
        if not self.cameras:
            raise ValueError("At least one camera must be configured")
        return self

    def camera(self, name_or_slug: str | None = None) -> CameraProfile:
        if name_or_slug is None:
            return self.cameras[0]
        for cam in self.cameras:
            if name_or_slug in (cam.name, cam.slug):
                return cam
        raise KeyError(
            f"No such camera '{name_or_slug}'. Available: {[c.name for c in self.cameras]}"
        )

    def path(self, relative: str) -> Path:
        p = Path(relative)
        return p if p.is_absolute() else self.root / p

    # -- loading -------------------------------------------------------------
    @staticmethod
    def _normalize(data: dict[str, Any]) -> dict[str, Any]:
        """Reduces the old and the new file layout to a single shape."""
        data = copy.deepcopy(data or {})
        defaults = data.pop("defaults", {}) or {}

        if "cameras" not in data:
            # old layout: a single camera described by top-level keys
            legacy = {k: data.pop(k) for k in LEGACY_KEYS if k in data}
            cam = data.pop("camera", {}) or {}
            merged = _deep_merge(legacy, cam)
            merged.setdefault("name", cam.get("name", "camera"))
            data["cameras"] = [merged]
        else:
            data["cameras"] = [_deep_merge(defaults, cam) for cam in data["cameras"]]
            for key in LEGACY_KEYS + ("camera",):
                data.pop(key, None)
        return data

    @classmethod
    def load(cls, path: str | Path = "config.yaml") -> Config:
        path = Path(path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {path}")
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        cfg = cls.model_validate(cls._normalize(raw))
        cfg.root = path.parent
        return cfg
