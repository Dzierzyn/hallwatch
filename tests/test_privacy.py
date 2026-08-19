"""Privacy masks: no pixel outside the mask may change, everything inside must."""

from __future__ import annotations

import numpy as np

from hallwatch.config import MaskCfg
from hallwatch.privacy import PrivacyMasker

RIGHT_HALF = [[0.5, 0.0], [1.0, 0.0], [1.0, 1.0], [0.5, 1.0]]


def frame_with_detail() -> np.ndarray:
    rng = np.random.default_rng(1)
    return rng.integers(0, 255, (200, 400, 3), dtype=np.uint8)


def test_masked_region_changes_and_rest_is_untouched():
    for mode in ("blur", "black", "pixelate"):
        masker = PrivacyMasker([MaskCfg(name="m", polygon=RIGHT_HALF, mode=mode)])
        original = frame_with_detail()
        out = masker.apply(original.copy())
        inside = np.abs(out[:, 220:].astype(int) - original[:, 220:].astype(int)).mean()
        outside = np.abs(out[:, :180].astype(int) - original[:, :180].astype(int)).mean()
        assert inside > 1.0, f"{mode}: mask did nothing"
        assert outside == 0.0, f"{mode}: leaked outside the mask"


def test_empty_masks_is_a_noop():
    masker = PrivacyMasker([])
    frame = frame_with_detail()
    assert masker.apply(frame) is frame  # same object, zero cost
    assert not masker.active


def test_contains_rejects_points_inside_mask():
    masker = PrivacyMasker([MaskCfg(name="m", polygon=RIGHT_HALF)])
    assert masker.contains((300, 100), (200, 400))
    assert not masker.contains((100, 100), (200, 400))
