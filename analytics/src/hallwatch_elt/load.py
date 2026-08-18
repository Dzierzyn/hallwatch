"""Load: Parquet -> GCS -> BigQuery jako tabele ZEWNETRZNE.

Decyzja architektoniczna. Zamiast wgrywac wiersze do tabel natywnych,
wystawiamy nad plikami w GCS tabele zewnetrzne z partycjonowaniem Hive.
Powody:

  * idempotentnosc z definicji - ponowne uruchomienie nadpisuje pliki, a nie
    dokleja wierszy. Przy zwyklym WRITE_APPEND kazdy retry Airflowa
    duplikowalby dane, i to cicho.
  * symetria z dev - lokalnie DuckDB czyta te same pliki Parquet wprost.
    Ten sam uklad w obu srodowiskach oznacza mniej niespodzianek na produkcji.
  * koszt - placimy za skan, nie za magazyn w BigQuery.

W dev ta warstwa nic nie robi: DuckDB siega do plikow bezposrednio.
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
    """Wysyla lokalne partycje Parquet do GCS, zachowujac uklad katalogow."""
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
                continue  # juz tam jest, nie placmy za transfer drugi raz
            blob.upload_from_filename(str(path))
            uploaded += 1
    log.info("GCS: wyslano %d nowych plikow do gs://%s", uploaded, cfg.gcs_bucket)
    return uploaded


def ensure_datasets(cfg: Settings | None = None) -> None:
    cfg = cfg or settings
    from google.cloud import bigquery

    client = bigquery.Client(project=cfg.gcp_project)
    for suffix in ("raw", "staging", "intermediate", "marts", "ml"):
        ds = bigquery.Dataset(f"{cfg.gcp_project}.{cfg.bq_dataset}_{suffix}")
        ds.location = cfg.bq_location
        client.create_dataset(ds, exists_ok=True)
        log.info("Dataset gotowy: %s_%s", cfg.bq_dataset, suffix)


def create_external_tables(cfg: Settings | None = None) -> list[str]:
    """Definiuje tabele zewnetrzne nad Parquet w GCS (partycjonowanie Hive po dt)."""
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
        hive.mode = "AUTO"                    # typ kolumny dt wykryty automatycznie
        hive.source_uri_prefix = uri_prefix   # wszystko po tym prefiksie to partycje
        ext.hive_partitioning = hive
        ext.autodetect = True

        table_id = f"{cfg.gcp_project}.{cfg.bq_dataset}_raw.{table}"
        try:  # podmieniamy definicje, gdyby zmienil sie schemat
            client.delete_table(table_id)
        except NotFound:
            pass
        tbl = bigquery.Table(table_id)
        tbl.external_data_configuration = ext
        client.create_table(tbl)
        made.append(table_id)
        log.info("Tabela zewnetrzna: %s -> %s", table_id, uri_prefix)
    return made


def run(cfg: Settings | None = None) -> dict:
    cfg = cfg or settings
    if not cfg.is_bigquery:
        log.info("target=dev: DuckDB czyta Parquet wprost, load nie jest potrzebny")
        return {"skipped": True}
    if not (cfg.gcp_project and cfg.gcs_bucket):
        raise RuntimeError("Ustaw HW_GCP_PROJECT i HW_GCS_BUCKET dla targetu prod")

    ensure_datasets(cfg)
    uploaded = sync_to_gcs(cfg)
    tables = create_external_tables(cfg)
    return {"uploaded_files": uploaded, "external_tables": tables}
