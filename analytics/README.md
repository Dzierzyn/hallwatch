# HallWatch Analytics: ELT + dbt + ML

A data layer on top of the [HallWatch](../README.md) computer-vision pipeline. Camera events
(people in a corridor, vehicles on a street) land in a warehouse, go through dbt modelling, and
at the end a model forecasts traffic and flags anomalies.

*(Polish original of this document: [`README.pl.md`](README.pl.md))*

```text
   CV pipeline                ELT                    warehouse             ML
┌──────────────┐   ┌──────────────────┐   ┌────────────────────┐   ┌──────────────┐
│ SQLite       │──▶│ extract          │──▶│ dbt                │──▶│ 24h forecast │
│ events       │   │ watermark        │   │ staging            │   │ anomalies    │
│ crossings    │   │ Parquet dt=...   │   │ intermediate       │   └──────┬───────┘
│ minute_stats │   └────────┬─────────┘   │ marts              │          │
└──────────────┘            │             └────────────────────┘          │
                            ▼                        ▲                    │
                   GCS + external tables             └────────────────────┘
                   BigQuery (prod)                    mart_traffic_monitor
```

Orchestration: Airflow (`dags/hallwatch_elt_dag.py`).

## Two environments, one codebase

| | `HW_TARGET=dev` | `HW_TARGET=prod` |
|---|---|---|
| Warehouse | DuckDB on disk | BigQuery |
| Raw data | Parquet read directly | Parquet in GCS + external tables |
| Cost | zero | data scanned |
| Purpose | development and tests with no cloud account | the real deployment |

This is not a convenience, it is a precondition for working sensibly: the whole pipeline can be
run and tested locally, and **the same code** runs on BigQuery. Dialect differences are confined
to `dbt/macros/portable.sql`.

## Getting started

```bash
make install
make seed      # 90 days of synthetic history with realistic seasonality
make all       # extract → dbt build → ML → dbt post_ml
```

Switching to BigQuery:

```bash
cp .env.example .env && $EDITOR .env      # HW_GCP_PROJECT, HW_GCS_BUCKET
gcloud auth application-default login
set -a && . ./.env && set +a
make bq-setup
HW_TARGET=prod make all
```

## Design decisions

**Incremental extract with a watermark, not a full reload.** Events are immutable once closed and
the database grows without bound. We take only what is new, into a `dt=YYYY-MM-DD` partition. The
filename is deterministic with respect to the range, so a repeated run overwrites the partition
instead of appending rows.

**On BigQuery, external tables over GCS rather than loading into native tables.** Idempotency then
follows from the design: an Airflow retry overwrites files rather than duplicating data, whereas
`WRITE_APPEND` would duplicate it, and would do so silently. The layout is also symmetric with dev,
where DuckDB reads the same files.

**Time zone stated explicitly in both dialects.** DuckDB computes `extract(hour)` in the session
zone, BigQuery in UTC. Without the `to_local()` macro the same code would produce a **different
daily profile** in dev and in prod, and it would only be caught by someone who compared the charts.
Hourly analytics is computed in local time, because "the 7 a.m. peak" should mean 7 a.m. for the
person who lives there.

**Sampling is a property of the camera, not of the row.** The street camera observes 5 minutes per
hour, so each of its events carries weight `1/duty_cycle`. If the weight were taken from the row,
hours with no observed traffic would have weight 1 and drop out of the extrapolation, systematically
**overstating** average intensity. In the first version that produced 29 events/h against a true 19.
The weight is therefore fixed per camera and stretched across the whole time spine.

**The time spine fills in the zeros.** An hour with no traffic is not missing data, it is the
information "zero". Without `int_hour_spine` the model would never see the quiet of the night and
would systematically overstate its forecasts.

**Direct forecasting, not recursive.** The model receives only features known 24 hours before the
forecast hour (lags of 24 h or more). It is never fed its own predictions, so error does not
accumulate, and the offline evaluation matches what the model will see in production.

**MAE, not MAPE.** MAPE divides by the actual value, and at night traffic is zero, so the
percentages explode and the metric lies. Every result is reported against a naive seasonal forecast
("the same as this hour last week"), because without a reference point any MAE number sounds clever.

**Anomalies via median and MAD, not mean and standard deviation.** Anomalies are by definition
extreme values, and with a plain standard deviation they would inflate the scale themselves and hide
from the detector.

## Results on demo data

```
corridor   MAE 1.000 vs naive 1.188  -> 15.8% better   (train=1655 test=336)
street     MAE 4.903 vs naive 6.286  -> 22.0% better   (train=1655 test=336)
anomalies  38 of 672 hours (5.65%)
dbt        PASS=32 (9 models + 23 tests)
```

The anomaly detector finds exactly the spikes the generator injected into the data, which makes this
a correctness test rather than only a demonstration.

## dbt tests

23 tests: primary keys, the `crossings → events` relationship, accepted-value sets, non-negativity of
counts, uniqueness of the hourly mart's grain, and a business test `assert_crossings_match_counts`,
where the number of recorded crossings must agree with the event counters. A mismatch means the CV
pipeline dropped a write.

## Environment notes

Run Airflow through `make airflow-up` (Docker). Executing Airflow 3 tasks locally on SQLite can hang
on macOS. The DAG parses correctly, and every step is runnable without an orchestrator through
`hw-elt`, which is deliberate: the Airflow tasks are thin and call the same functions as the CLI.

## Layout

```text
analytics/
  src/hallwatch_elt/
    config.py      settings from the environment, absolute paths
    extract.py     SQLite → Parquet, watermark
    load.py        Parquet → GCS → BigQuery external tables
    seed.py        synthetic history generator
    warehouse.py   one interface over DuckDB and BigQuery
    ml/            features, forecasting, anomalies
    cli.py         hw-elt: the same steps as in the DAG
  dbt/
    macros/portable.sql   dialect differences in one place
    models/staging|intermediate|marts
    tests/                business tests
  dags/hallwatch_elt_dag.py
  docker-compose.yaml     Airflow + Postgres
```
