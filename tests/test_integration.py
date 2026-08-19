"""Full-pipeline tests: need ffmpeg and download YOLO weights on first run."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_selftest_end_to_end(tmp_path, monkeypatch):
    """The shipped selftest is the canonical end-to-end check; run it as pytest."""
    from hallwatch.config import Config
    from hallwatch.tools import selftest

    cfg = Config.model_validate({"cameras": [{"name": "testcam", "source": "0"}]})
    cfg.root = tmp_path
    assert selftest(cfg)
