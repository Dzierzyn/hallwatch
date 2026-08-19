"""Load: Parquet -> GCS -> BigQuery as EXTERNAL tables.

An architectural decision. Instead of loading rows into native tables, we expose
external tables with Hive partitioning over the files in GCS. Reasons:

  * idempotency by construction: a rerun overwrites files rather than appending
    rows. With a plain WRITE_APPEND every Airflow retry would duplicate data,
    and would do it silently.
  * symmetry with dev: locally DuckDB reads the same Parquet files directly.
    The same layout in both environments means fewer surprises in production.
  * cost: we pay for scanning, not for storage inside BigQuery.

In dev this layer does nothing, because DuckDB reaches for the files directly.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import Settings, settings

log = logging.getLogger(__name__)

RAW_TABLES = ("events", "crossings", "minute_stats")


def _gcs_prefix(cfg: Settings, table: str) -> str:
    return f"raw/{table}/"


def sync_to_gcs(cfg: Settings | None = None) -> int:
    """Uploads local Parquet partitions to GCS, preserving the directory layout."""
    cfg = cfg or settings
    from google.cloud import storage

    client = storage.Client(project=cfg.gcp_project)
    bucket = client.bucket(cfg.gcs_bucket)
    uploaded = 0

    for table in RAW_TABLES:
        local_root = cfg.raw_dir / table
        if not local_root.exists():
            continue
        for path in sorted(local_root.rglob("*.parquet")):
            rel = path.relative_to(local_root).as_posix()  # dt=YYYY-MM-DD/part-...
            blob = bucket.blob(f"{_gcs_prefix(cfg, table)}{rel}")
            if blob.exists() and blob.size == path.stat().st_size:
                continue  # already there, no need to pay for the transfer twice
            blob.upload_from_filename(str(path))
            uploaded += 1
    log.info("GCS: uploaded %d new files to gs://%s", uploaded, cfg.gcs_bucket)
    return uploaded


def ensure_datasets(cfg: Settings | None = None) -> None:
    cfg = cfg or settings
    from google.cloud import bigquery

    client = bigquery.Client(project=cfg.gcp_project)
    for suffix in ("raw", "staging", "intermediate", "marts", "ml"):
        ds = bigquery.Dataset(f"{cfg.gcp_project}.{cfg.bq_dataset}_{suffix}")
        ds.location = cfg.bq_location
        client.create_dataset(ds, exists_ok=True)
        log.info("Dataset ready: %s_%s", cfg.bq_dataset, suffix)


def create_external_tables(cfg: Settings | None = None) -> list[str]:
    """Defines external tables over Parquet in GCS (Hive partitioning by dt)."""
    cfg = cfg or settings
    from google.cloud import bigquery
    from google.api_core.exceptions import NotFound

    client = bigquery.Client(project=cfg.gcp_project)
    made = []
    for table in RAW_TABLES:
        uri_prefix = f"gs://{cfg.gcs_bucket}/{_gcs_prefix(cfg, table)}"
        ext = bigquery.ExternalConfig("PARQUET")
        ext.source_uris = [f"{uri_prefix}*"]
        hive = bigquery.HivePartitioningOptions()
        hive.mode = "AUTO"                    # dt column type detected automatically
        hive.source_uri_prefix = uri_prefix   # everything after this prefix is a partition
        ext.hive_partitioning = hive
        ext.autodetect = True

        table_id = f"{cfg.gcp_project}.{cfg.bq_dataset}_raw.{table}"
        try:  # replace the definition in case the schema changed
            client.delete_table(table_id)
        except NotFound:
            pass
        tbl = bigquery.Table(table_id)
        tbl.external_data_configuration = ext
        client.create_table(tbl)
        made.append(table_id)
        log.info("External table: %s -> %s", table_id, uri_prefix)
    return made


def run(cfg: Settings | None = None) -> dict:
    cfg = cfg or settings
    if not cfg.is_bigquery:
        log.info("target=dev: DuckDB reads Parquet directly, load is not needed")
        return {"skipped": True}
    if not (cfg.gcp_project and cfg.gcs_bucket):
        raise RuntimeError("Set HW_GCP_PROJECT and HW_GCS_BUCKET for the prod target")

    ensure_datasets(cfg)
    uploaded = sync_to_gcs(cfg)
    tables = create_external_tables(cfg)
    return {"uploaded_files": uploaded, "external_tables": tables}
