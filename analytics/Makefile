PY := .venv/bin/python
export PYTHONPATH := src
export HW_TARGET ?= dev

.PHONY: install seed elt dbt-build dbt-test dbt-docs ml all clean bq-setup airflow-up airflow-down verify

install:
	uv venv --python 3.12
	uv pip install --python $(PY) -e ".[dbt]"
	@echo "\nGotowe. Nastepny krok: make seed && make all"

# --- lokalnie (target=dev, DuckDB) ------------------------------------------
seed:                     ## syntetyczna historia 90 dni do dema i testow ML
	$(PY) -m hallwatch_elt.cli seed --days 90

elt:
	$(PY) -m hallwatch_elt.cli extract

dbt-build:
	cd dbt && ../$(PY) -m dbt.cli.main build --profiles-dir . --exclude tag:post_ml

ml:
	$(PY) -m hallwatch_elt.cli ml

dbt-post:
	cd dbt && ../$(PY) -m dbt.cli.main build --profiles-dir . --select tag:post_ml

all:                      ## caly pipeline end-to-end
	$(PY) -m hallwatch_elt.cli all

dbt-docs:
	cd dbt && ../$(PY) -m dbt.cli.main docs generate --profiles-dir . && \
		../$(PY) -m dbt.cli.main docs serve --profiles-dir . --port 8081

verify:                   ## smoke test calego stosu na czystych danych
	$(PY) -m hallwatch_elt.cli seed --days 90
	$(PY) -m hallwatch_elt.cli all --full-refresh

# --- BigQuery (target=prod) -------------------------------------------------
bq-setup:                 ## utworz datasety i bucket; wymaga HW_GCP_PROJECT i HW_GCS_BUCKET
	@test -n "$(HW_GCP_PROJECT)" || (echo "Ustaw HW_GCP_PROJECT" && exit 1)
	@test -n "$(HW_GCS_BUCKET)"  || (echo "Ustaw HW_GCS_BUCKET" && exit 1)
	gcloud storage buckets create gs://$(HW_GCS_BUCKET) \
		--project=$(HW_GCP_PROJECT) --location=$(HW_BQ_LOCATION) 2>/dev/null || true
	HW_TARGET=prod $(PY) -c "from hallwatch_elt import load; load.ensure_datasets()"
	@echo "Teraz: HW_TARGET=prod make all"

# --- Airflow ----------------------------------------------------------------
airflow-up:
	docker compose up -d --build
	@echo "UI: http://localhost:8080  (admin/admin)"

airflow-down:
	docker compose down

clean:
	rm -rf data/raw data/warehouse.duckdb .state dbt/target dbt/logs
