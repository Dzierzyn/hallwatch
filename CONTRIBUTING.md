# Contributing to HallWatch

Thanks for considering a contribution! This project aims to be the easiest way
to turn *any* camera into a private, smart monitoring tool - contributions
that reduce friction for newcomers are the most valuable ones.

## Quick setup

```bash
git clone https://github.com/Dzierzyn/hallwatch
cd hallwatch
make install         # uv venv + editable install (or: pip install -e .)
make test            # pytest, no camera or GPU needed
```

Prerequisites: Python 3.10-3.13, `ffmpeg` on PATH. `uv` is recommended but
plain `pip` works.

## Before you open a PR

```bash
uvx ruff check src tests --fix
uvx ruff format src tests
.venv/bin/python -m pytest tests -m "not integration" -q
```

CI runs the same three commands on Linux, macOS and Windows. The integration
suite (`pytest -m integration`) downloads YOLO weights and needs ffmpeg - run
it if you touched the pipeline, recorder or detector.

## What makes a good PR here

- **Camera compatibility reports** - even without code. If you got HallWatch
  working with a camera we don't list (or failed to), open an issue with the
  model and the RTSP URL format. This is the single most useful contribution.
- **Friction removal** - clearer errors, better defaults, docs for a platform
  we cover poorly (Windows, Raspberry Pi).
- **Focused changes** - one topic per PR. Refactors welcome, but separate from
  behaviour changes.

## Design principles (please keep them)

1. **Privacy first**: masks apply before detection; masked pixels never reach
   disk or cloud. Nothing may weaken this.
2. **Runs anywhere**: CPU-only laptops and Raspberry Pi are first-class
   targets. Features must degrade gracefully without a GPU.
3. **No accounts, no phone-home**: everything works offline; cloud backup and
   notifications are opt-in and self-chosen.
4. **Explained decisions**: non-obvious engineering choices get a short
   comment saying *why* (see `recorder.py` or `counter.py` for the tone).

## Code style

Enforced by ruff (config in `ruff.toml`); comments explain *why*, not *what*.
English everywhere.
