# Airflow image with our ELT package and dbt inside.
# We deliberately do NOT install torch or the rest of the CV stack here - the
# orchestrator does not need to run YOLO, it only calls steps on ready data.
FROM apache/airflow:3.1.2-python3.12

USER root
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
USER airflow

COPY --chown=airflow:root pyproject.toml /opt/hallwatch/
COPY --chown=airflow:root src /opt/hallwatch/src
RUN pip install --no-cache-dir -e /opt/hallwatch \
    && pip install --no-cache-dir "dbt-core>=1.9" "dbt-bigquery>=1.9" "dbt-duckdb>=1.9"
