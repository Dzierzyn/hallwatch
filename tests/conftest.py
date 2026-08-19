"""Shared fixtures. Unit tests run without YOLO or ffmpeg; anything that needs
them is marked `integration` and skipped automatically when they are missing."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: needs ffmpeg and downloads YOLO weights (slow)"
    )


def pytest_collection_modifyitems(config, items):
    if shutil.which("ffmpeg") is None:
        skip = pytest.mark.skip(reason="ffmpeg not found in PATH")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip)


@pytest.fixture()
def tmp_config(tmp_path: Path):
    """Minimal single-camera config rooted in a temp dir."""
    from hallwatch.config import Config

    cfg = Config.model_validate(
        {
            "cameras": [{"name": "testcam", "source": "0"}],
            "storage": {"db": "data/test.db"},
        }
    )
    cfg.root = tmp_path
    return cfg
