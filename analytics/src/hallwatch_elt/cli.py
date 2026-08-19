"""ELT CLI: one way of running the steps, locally and from Airflow.

Airflow calls the same functions a human calls in the terminal. That way the
pipeline can be debugged without bringing up an orchestrator, and the tasks hold
no logic that cannot be reproduced by hand.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

from .config import ROOT, settings

log = logging.getLogger(__name__)


def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-24s %(message)s",
        datefmt="%H:%M:%S",
    )


def task_seed(days: int = 90) -> dict:
    from .seed import generate

    return generate(settings.source_db, days=days)


def task_extract(full_refresh: bool = False) -> dict:
    from . import extract

    return extract.run(settings, full_refresh=full_refresh)


def task_load() -> dict:
    from . import load

    return load.run(settings)


def task_dbt(*args: str) -> int:
    """Runs dbt from the project directory, inheriting the target from the environment."""
    cmd = [sys.executable.replace("python", "dbt"), *args, "--profiles-dir", "."]
    if not Path(cmd[0]).exists():
        cmd[0] = str(ROOT / ".venv" / "bin" / "dbt")
    # dbt inherits our configuration through the environment, but with paths
    # already resolved, otherwise '../data/...' would be relative to dbt/
    env = {
        **os.environ,
        "HW_TARGET": settings.target,
        "HW_DUCKDB": str(settings.duckdb_path),
        "HW_GCP_PROJECT": settings.gcp_project,
        "HW_BQ_DATASET": settings.bq_dataset,
        "HW_BQ_LOCATION": settings.bq_location,
        "DBT_RAW_DIR": str(settings.raw_dir),
    }
    log.info("dbt %s (target=%s)", " ".join(args), settings.target)
    result = subprocess.run([*cmd, "--vars", f"{{raw_dir: '{settings.raw_dir}'}}"],
                            cwd=ROOT / "dbt", env=env)
    if result.returncode != 0:
        raise RuntimeError(f"dbt {' '.join(args)} exited with code {result.returncode}")
    return result.returncode


def task_ml() -> dict:
    from .ml import anomaly, forecast
    from .warehouse import Warehouse

    wh = Warehouse(settings)
    hourly = wh.read("agg_hourly_traffic")
    preds, metrics = forecast.run(hourly)
    if preds.empty:
        raise RuntimeError("Model returned no predictions - not enough data?")
    anomalies = anomaly.run(preds, hourly)

    wh.write("ml_hourly_forecast", preds)
    wh.write("ml_hourly_anomaly", anomalies)
    wh.write("ml_model_metrics", forecast.metrics_frame(metrics))
    return {
        "predictions": len(preds),
        "anomalies": int(anomalies["is_anomaly"].sum()) if not anomalies.empty else 0,
        "metrics": [m.__dict__ for m in metrics],
    }


def task_all(full_refresh: bool = False) -> dict:
    out = {"extract": task_extract(full_refresh), "load": task_load()}
    task_dbt("build", "--exclude", "tag:post_ml")
    out["ml"] = task_ml()
    task_dbt("build", "--select", "tag:post_ml")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="hw-elt", description="ELT + ML over HallWatch data")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("seed", help="generate synthetic demo history")
    s.add_argument("--days", type=int, default=90)
    e = sub.add_parser("extract", help="SQLite -> Parquet (incremental)")
    e.add_argument("--full-refresh", action="store_true")
    sub.add_parser("load", help="Parquet -> GCS -> BigQuery external tables")
    sub.add_parser("ml", help="forecast + anomalies")
    a = sub.add_parser("all", help="the whole pipeline end-to-end")
    a.add_argument("--full-refresh", action="store_true")

    args = p.parse_args(argv)
    _setup_logging(args.verbose)
    log.info("Configuration: %s", settings.describe())

    match args.cmd:
        case "seed":
            print(task_seed(args.days))
        case "extract":
            print(task_extract(args.full_refresh))
        case "load":
            print(task_load())
        case "ml":
            print(task_ml())
        case "all":
            print(task_all(args.full_refresh))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
