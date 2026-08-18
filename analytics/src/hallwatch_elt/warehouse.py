"""Jeden interfejs do hurtowni - DuckDB w dev, BigQuery w prod.

Kod ML nie ma prawa wiedziec, gdzie leza dane. Dzieki temu ten sam model
trenuje sie lokalnie na DuckDB i produkcyjnie na BigQuery bez zmiany linijki.
"""

from __future__ import annotations

import logging

import pandas as pd

from .config import Settings, settings

log = logging.getLogger(__name__)


class Warehouse:
    def __init__(self, cfg: Settings | None = None) -> None:
        self.cfg = cfg or settings

    # -- odczyt -------------------------------------------------------------
    def read(self, relation: str) -> pd.DataFrame:
        if self.cfg.is_bigquery:
            from google.cloud import bigquery

            client = bigquery.Client(project=self.cfg.gcp_project)
            fq = f"`{self.cfg.gcp_project}.{self.cfg.bq_dataset}_marts.{relation}`"
            return client.query(f"SELECT * FROM {fq}").to_dataframe()

        import duckdb

        con = duckdb.connect(str(self.cfg.duckdb_path), read_only=True)
        try:
            return con.execute(f"SELECT * FROM main_marts.{relation}").df()
        finally:
            con.close()

    # -- zapis --------------------------------------------------------------
    def write(self, relation: str, df: pd.DataFrame) -> int:
        if df.empty:
            log.warning("Nic do zapisania w %s", relation)
            return 0

        if self.cfg.is_bigquery:
            from google.cloud import bigquery

            client = bigquery.Client(project=self.cfg.gcp_project)
            dataset = f"{self.cfg.gcp_project}.{self.cfg.bq_dataset}_ml"
            client.create_dataset(
                bigquery.Dataset(dataset), exists_ok=True
            ).location = self.cfg.bq_location
            table_id = f"{dataset}.{relation}"
            job = client.load_table_from_dataframe(
                df,
                table_id,
                job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE"),
            )
            job.result()
            log.info("BigQuery %s <- %d wierszy", table_id, len(df))
            return len(df)

        import duckdb

        con = duckdb.connect(str(self.cfg.duckdb_path))
        try:
            con.execute("CREATE SCHEMA IF NOT EXISTS main_ml")
            con.register("_incoming", df)
            con.execute(f"CREATE OR REPLACE TABLE main_ml.{relation} AS SELECT * FROM _incoming")
            con.unregister("_incoming")
            log.info("DuckDB main_ml.%s <- %d wierszy", relation, len(df))
            return len(df)
        finally:
            con.close()
