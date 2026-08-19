"""One interface to the warehouse: DuckDB in dev, BigQuery in prod.

The ML code has no business knowing where the data lives. That way the same model
trains locally on DuckDB and in production on BigQuery without changing a line.
"""

from __future__ import annotations

import logging

import pandas as pd

from .config import Settings, settings

log = logging.getLogger(__name__)


class Warehouse:
    def __init__(self, cfg: Settings | None = None) -> None:
        self.cfg = cfg or settings

    # -- read ----------------------------------------------------------------
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

    # -- write ---------------------------------------------------------------
    def write(self, relation: str, df: pd.DataFrame) -> int:
        if df.empty:
            log.warning("Nothing to write to %s", relation)
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
            log.info("BigQuery %s <- %d rows", table_id, len(df))
            return len(df)

        import duckdb

        con = duckdb.connect(str(self.cfg.duckdb_path))
        try:
            con.execute("CREATE SCHEMA IF NOT EXISTS main_ml")
            con.register("_incoming", df)
            con.execute(f"CREATE OR REPLACE TABLE main_ml.{relation} AS SELECT * FROM _incoming")
            con.unregister("_incoming")
            log.info("DuckDB main_ml.%s <- %d rows", relation, len(df))
            return len(df)
        finally:
            con.close()
