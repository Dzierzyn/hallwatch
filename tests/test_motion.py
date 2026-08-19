"""MOG2 gate: quiet scene stays quiet, a moving object trips it."""

from __future__ import annotations

import numpy as np

from hallwatch.config import MotionCfg
from hallwatch.motion import MotionDetector


def test_static_scene_no_motion_after_warmup():
    md = MotionDetector(MotionCfg(warmup_frames=5))
    rng = np.random.default_rng(0)
    base = rng.integers(90, 110, (240, 320, 3)).astype(np.uint8)
    result = None
    for _ in range(30):
        noisy = base + rng.integers(0, 3, base.shape, dtype=np.uint8)
        result = md.update(noisy)
    assert result is not None and not result.detected


def test_moving_block_detected():
    md = MotionDetector(MotionCfg(warmup_frames=5))
    rng = np.random.default_rng(0)
    base = rng.integers(90, 110, (240, 320, 3)).astype(np.uint8)
    for _ in range(20):  # learn the background
        md.update(base + rng.integers(0, 3, base.shape, dtype=np.uint8))
    hits = 0
    for i in range(10):
        frame = base + rng.integers(0, 3, base.shape, dtype=np.uint8)
        x = 40 + i * 15
        frame[80:180, x : x + 50] = 255
        if md.update(frame).detected:
            hits += 1
    assert hits >= 5


def test_disabled_gate_always_passes():
    md = MotionDetector(MotionCfg(enabled=False))
    frame = np.zeros((100, 100, 3), np.uint8)
    assert md.update(frame).detected
