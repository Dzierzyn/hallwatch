# Obraz Airflowa z naszym pakietem ELT i dbt w srodku.
# Celowo NIE instalujemy tu torcha ani reszty stosu CV - orkiestrator
# nie musi umiec liczyc YOLO, ma tylko wolac kroki na gotowych danych.
FROM apache/airflow:3.1.2-python3.12

USER root
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
USER airflow

COPY --chown=airflow:root pyproject.toml /opt/hallwatch/
COPY --chown=airflow:root src /opt/hallwatch/src
RUN pip install --no-cache-dir -e /opt/hallwatch \
    && pip install --no-cache-dir "dbt-core>=1.9" "dbt-bigquery>=1.9" "dbt-duckdb>=1.9"
