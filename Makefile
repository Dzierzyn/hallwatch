PY := .venv/bin/python
PYTHON ?= python3

# Belt and braces: even if the editable install misbehaves (see fix-pth),
# PYTHONPATH keeps the package importable straight from the repo.
export PYTHONPATH := src

.PHONY: install install-pip fix-pth test test-all lint run probe scan zones wake prune clean

install:  ## create venv + install (requires uv: https://docs.astral.sh/uv/)
	uv venv --python 3.12
	uv pip install -e ".[dev]"
	$(MAKE) fix-pth
	@echo "\nDone. Next: make test && make run"

# Needs Python 3.10-3.13. If your system python3 is newer:
#   PYTHON=python3.12 make install-pip
# (fix-pth is not needed here - the hidden-.pth bug is uv-specific)
install-pip:  ## same, but with plain python/pip (no uv needed)
	$(PYTHON) -m venv .venv
	$(PY) -m pip install -e ".[dev]"
	@echo "\nDone. Next: make test && make run"

# macOS + uv + Python >= 3.12 only: uv sets the hidden flag on .pth files and
# site.py deliberately skips hidden .pth, so editable installs silently fail
# with ModuleNotFoundError. Harmless everywhere else.
fix-pth:
	@if [ "$$(uname -s)" = "Darwin" ]; then \
		chflags nohidden .venv/lib/python3.*/site-packages/*.pth 2>/dev/null || true; \
	fi

test:  ## fast unit tests (no camera, no GPU, no ffmpeg needed)
	$(PY) -m pytest tests -m "not integration" -q

test-all:  ## + full pipeline on synthetic video (needs ffmpeg, downloads YOLO ~6 MB)
	$(PY) -m pytest tests -q
	$(PY) -m hallwatch selftest

lint:
	uvx ruff check src tests
	uvx ruff format --check src tests

run:  ## start the pipeline + dashboard (http://127.0.0.1:8000)
	$(PY) -m hallwatch run

probe:  ## test a stream: make probe SOURCE='rtsp://...'
	$(PY) -m hallwatch probe $(if $(SOURCE),--source '$(SOURCE)')

scan:  ## find RTSP cameras on your LAN
	$(PY) -m hallwatch scan

zones:  ## draw counting lines / zones / privacy masks by clicking
	$(PY) -m hallwatch zones

wake:  ## wake an on_demand (battery) camera
	$(PY) -m hallwatch wake

prune:  ## delete recordings older than retention_days
	$(PY) -m hallwatch prune

clean:
	rm -rf data/selftest .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
