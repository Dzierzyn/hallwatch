"""CLI: hallwatch <run|probe|scan|zones|selftest|prune>"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import Config


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-18s %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("uvicorn.access", "botocore", "urllib3", "ultralytics"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def cmd_run(args: argparse.Namespace, cfg: Config) -> int:
    import uvicorn

    from .pipeline import PipelineManager
    from .web import create_app

    if args.camera:
        cfg.cameras = [cfg.camera(args.camera)]
    if args.source:
        cfg.cameras[0].source = args.source
    if args.mode:
        for cam in cfg.cameras:
            cam.mode = args.mode
    if args.no_record:
        for cam in cfg.cameras:
            cam.recording.enabled = False

    manager = PipelineManager(cfg).start()
    app = create_app(cfg, manager)
    url = f"http://{cfg.web.host}:{cfg.web.port}"
    cams = ", ".join(f"{c.name} [{c.mode}]" for c in cfg.cameras)
    print(f"\n  HallWatch running  ->  {url}\n  Cameras: {cams}\n  Ctrl+C konczy\n")
    try:
        uvicorn.run(app, host=cfg.web.host, port=cfg.web.port, log_level="warning")
    except KeyboardInterrupt:
        pass
    finally:
        print("\nShutting down...")
        manager.stop()
    return 0


def cmd_probe(args: argparse.Namespace, cfg: Config) -> int:
    from .tools import probe_source

    source = args.source or cfg.camera(args.camera).source
    return 0 if probe_source(source, seconds=args.seconds, preview=not args.no_preview) else 1


def cmd_scan(args: argparse.Namespace, cfg: Config) -> int:
    from .tools import scan_network

    scan_network(args.subnet)
    return 0


def cmd_zones(args: argparse.Namespace, cfg: Config) -> int:
    from .tools import edit_zones

    edit_zones(cfg, args.source, args.camera)
    return 0


def cmd_selftest(args: argparse.Namespace, cfg: Config) -> int:
    from .tools import selftest

    return 0 if selftest(cfg) else 1


def cmd_wake(args: argparse.Namespace, cfg: Config) -> int:
    """Budzi dzialajaca instancje (tryb on_demand)."""
    import requests

    url = f"http://{cfg.web.host}:{cfg.web.port}/api/wake"
    params = {"source": args.source_name}
    if args.camera:
        params["camera"] = args.camera
    try:
        resp = requests.post(url, params=params, timeout=10)
        data = resp.json()
        print(f"{'OK' if data.get('accepted') else 'POMINIETO'}: {data.get('detail')}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: cannot connect to {url} ({exc})", file=sys.stderr)
        print("Is 'hallwatch run' running?", file=sys.stderr)
        return 1


def cmd_prune(args: argparse.Namespace, cfg: Config) -> int:
    from .pipeline import prune

    files, events = prune(cfg)
    print(f"Removed {files} files from {events} events past their retention")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hallwatch",
        description="Corridor monitoring with computer vision: motion, people, audio, recordings",
    )
    parser.add_argument("-c", "--config", default="config.yaml", help="path to config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run the pipeline and dashboard")
    p_run.add_argument("--camera", help="run only the named camera")
    p_run.add_argument("--source", help="override the source from config (RTSP / 0 / file.mp4)")
    p_run.add_argument(
        "--mode",
        choices=["continuous", "on_demand", "sampling"],
        help="override camera.mode (on_demand = battery camera)",
    )
    p_run.add_argument("--no-record", action="store_true", help="do not save clips")
    p_run.set_defaults(func=cmd_run)

    p_probe = sub.add_parser("probe", help="check the stream and measure FPS")
    p_probe.add_argument("--source")
    p_probe.add_argument("--camera", help="camera from config (first one by default)")
    p_probe.add_argument("--seconds", type=float, default=6.0)
    p_probe.add_argument("--no-preview", action="store_true", help="no preview window")
    p_probe.set_defaults(func=cmd_probe)

    p_scan = sub.add_parser("scan", help="find IP cameras on the local network")
    p_scan.add_argument("--subnet", help="e.g. 192.168.1 (default: from the active interface)")
    p_scan.set_defaults(func=cmd_scan)

    p_zones = sub.add_parser("zones", help="interactively define the line, zones and masks")
    p_zones.add_argument("--source")
    p_zones.add_argument("--camera", help="camera from config (first one by default)")
    p_zones.set_defaults(func=cmd_zones)

    p_wake = sub.add_parser("wake", help="wake an instance running in on_demand mode")
    p_wake.add_argument("--source-name", default="cli", help="label for the signal source")
    p_wake.add_argument("--camera", help="which camera to wake")
    p_wake.set_defaults(func=cmd_wake)

    sub.add_parser("selftest", help="exercise the whole pipeline without a camera").set_defaults(
        func=cmd_selftest
    )
    sub.add_parser("prune", help="delete recordings older than retention_days").set_defaults(
        func=cmd_prune
    )

    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    try:
        cfg = Config.load(args.config)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Copy config.yaml from the repository or point at it with -c", file=sys.stderr)
        return 2

    return int(args.func(args, cfg))


if __name__ == "__main__":
    raise SystemExit(main())
