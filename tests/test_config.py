"""Config loading: multi-camera layout, defaults merging, legacy compatibility."""

from __future__ import annotations

import textwrap

import pytest

from hallwatch.config import CameraProfile, Config


def _load(tmp_path, text: str) -> Config:
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return Config.load(p)


def test_multi_camera_with_defaults_merging(tmp_path):
    cfg = _load(
        tmp_path,
        """
        defaults:
          width: 640
          detection: {conf: 0.5}
        cameras:
          - name: front door
            source: "0"
          - name: street
            source: "rtsp://example/stream"
            detection: {conf: 0.3, classes: [2, 7]}
        """,
    )
    assert [c.name for c in cfg.cameras] == ["front door", "street"]
    assert cfg.cameras[0].width == 640  # inherited
    assert cfg.cameras[0].detection.conf == 0.5  # inherited
    assert cfg.cameras[1].detection.conf == 0.3  # overridden
    assert cfg.cameras[1].detection.classes == [2, 7]
    assert cfg.cameras[0].slug == "front-door"  # spaces slugified


def test_legacy_single_camera_layout_still_loads(tmp_path):
    cfg = _load(
        tmp_path,
        """
        camera:
          name: hallway
          source: "0"
        detection: {conf: 0.25}
        recording: {dir: "data/clips"}
        """,
    )
    assert len(cfg.cameras) == 1
    cam = cfg.cameras[0]
    assert cam.name == "hallway"
    assert cam.detection.conf == 0.25
    assert cam.clip_dir == "data/clips/hallway"


def test_duplicate_camera_names_rejected(tmp_path):
    with pytest.raises(ValueError, match="[Dd]uplicate"):
        _load(
            tmp_path,
            """
            cameras:
              - {name: cam, source: "0"}
              - {name: cam, source: "1"}
            """,
        )


def test_camera_lookup_by_name_and_slug():
    cfg = Config.model_validate({"cameras": [{"name": "Front Door", "source": "0"}]})
    assert cfg.camera("Front Door").name == "Front Door"
    assert cfg.camera("front-door").name == "Front Door"
    with pytest.raises(KeyError):
        cfg.camera("nope")


def test_sampling_duty_cycle():
    cam = CameraProfile(name="s", sampling={"every_minutes": 60, "seconds": 300})
    assert cam.sampling.duty_cycle == pytest.approx(300 / 3600)


def test_per_camera_clip_dirs_do_not_collide():
    cfg = Config.model_validate(
        {"cameras": [{"name": "a", "source": "0"}, {"name": "b", "source": "1"}]}
    )
    assert cfg.cameras[0].clip_dir != cfg.cameras[1].clip_dir
