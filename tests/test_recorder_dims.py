"""Even-dimension guarantee: odd frame sizes must never reach the H.264 encoder.

libx264 + yuv420p rejects odd width/height; before the fix a portrait phone
camera scaled to width 960 produced an odd height, ffmpeg died instantly and
every clip was silently deleted as empty.
"""

from __future__ import annotations

import shutil

import numpy as np
import pytest

from hallwatch.capture import FrameSource


def test_clipwriter_floors_odd_dimensions_to_even(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not found in PATH")
    from hallwatch.recorder import ClipWriter

    writer = ClipWriter(tmp_path / "odd.mp4", 641, 361, fps=10)
    try:
        assert writer.width % 2 == 0 and writer.height % 2 == 0
        assert (writer.width, writer.height) == (640, 360)
    finally:
        writer.close()


def test_resize_produces_even_dimensions_for_portrait_frames():
    src = FrameSource("0", width=960)
    # 1080x1920 portrait: naive scaling gives height 1707 (odd)
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    out = src._resize(frame)
    assert out.shape[0] % 2 == 0 and out.shape[1] % 2 == 0
