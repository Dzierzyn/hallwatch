"""ELT configuration, entirely driven by environment variables.

The principle: the code does not know whether it is loading into DuckDB or into
BigQuery. It only knows its target, and the details live in one place.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # analytics/
REPO = ROOT.parent  # the hallwatch project directory


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Settings:
    # source: the SQLite database written by the CV pipeline
    source_db: Path = field(
        default_factory=lambda: Path(_env("HW_SOURCE_DB") or REPO / "data" / "hallwatch.db")
    )
    # landing zone: Parquet partitioned by date
    raw_dir: Path = field(
        default_factory=lambda: Path(_env("HW_RAW_DIR") or ROOT / "data" / "raw")
    )
    state_dir: Path = field(
        default_factory=lambda: Path(_env("HW_STATE_DIR") or ROOT / ".state")
    )
    # dev = DuckDB locally, prod = BigQuery
    target: str = field(default_factory=lambda: _env("HW_TARGET", "dev") or "dev")
    duckdb_path: Path = field(
        default_factory=lambda: Path(_env("HW_DUCKDB") or ROOT / "data" / "warehouse.duckdb")
    )
    # BigQuery
    gcp_project: str = field(default_factory=lambda: _env("HW_GCP_PROJECT"))
    bq_dataset: str = field(default_factory=lambda: _env("HW_BQ_DATASET", "hallwatch") or "hallwatch")
    bq_location: str = field(default_factory=lambda: _env("HW_BQ_LOCATION", "EU") or "EU")
    gcs_bucket: str = field(default_factory=lambda: _env("HW_GCS_BUCKET"))

    def __post_init__(self) -> None:
        # Paths MUST be absolute: dbt is run from the dbt/ subdirectory,
        # so every relative path would drift depending on
        # who happens to invoke the step. That kind of bug is silent and misleading.
        for field_name in ("source_db", "raw_dir", "state_dir", "duckdb_path"):
            value = Path(getattr(self, field_name))
            if not value.is_absolute():
                value = (ROOT / value).resolve()
            object.__setattr__(self, field_name, value)

    @property
    def is_bigquery(self) -> bool:
        return self.target == "prod"

    def ensure_dirs(self) -> None:
        for d in (self.raw_dir, self.state_dir, self.duckdb_path.parent):
            d.mkdir(parents=True, exist_ok=True)

    def describe(self) -> str:
        where = (
            f"BigQuery {self.gcp_project}.{self.bq_dataset} ({self.bq_location})"
            if self.is_bigquery
            else f"DuckDB {self.duckdb_path}"
        )
        return f"target={self.target} -> {where}"


settings = Settings()
