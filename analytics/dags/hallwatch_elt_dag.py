"""DAG: HallWatch ELT + ML.

Ksztalt przeplywu wynika z jednej zaleznosci: modele ML czytaja marty dbt,
a mart monitorujacy czyta wyniki ML. Stad dwa przebiegi dbt rozdzielone
krokiem ML - inaczej mielibysmy cykl w grafie.

    extract -> load -> dbt build (bez post_ml) -> ml -> dbt build (post_ml)

Zadania sa cienkie: wolaja te same funkcje, co CLI. Cala logika mieszka
w pakiecie hallwatch_elt, wiec da sie ja odpalic i zdebugowac bez Airflowa.
"""

from __future__ import annotations

import pendulum
from airflow.sdk import dag, task

DEFAULT_ARGS = {
    "owner": "hallwatch",
    "retries": 2,
    "retry_delay": pendulum.duration(minutes=5),
}


@dag(
    dag_id="hallwatch_elt",
    description="Zdarzenia z kamer -> BigQuery -> dbt -> prognoza i anomalie",
    schedule="15 * * * *",  # kwadrans po kazdej pelnej godzinie
    start_date=pendulum.datetime(2026, 1, 1, tz="Europe/Warsaw"),
    catchup=False,
    max_active_runs=1,  # przyrost ze znacznikiem wodnym nie znosi rownoleglosci
    default_args=DEFAULT_ARGS,
    tags=["hallwatch", "elt", "ml"],
)
def hallwatch_elt():
    @task
    def extract() -> dict:
        from hallwatch_elt.cli import task_extract

        return task_extract()

    @task
    def load(extracted: dict) -> dict:
        from hallwatch_elt.cli import task_load

        return task_load()

    @task
    def dbt_core(loaded: dict) -> str:
        """Staging, intermediate i marty - wszystko poza tym, co zalezy od ML."""
        from hallwatch_elt.cli import task_dbt

        task_dbt("build", "--exclude", "tag:post_ml")
        return "ok"

    @task
    def machine_learning(dbt_status: str) -> dict:
        from hallwatch_elt.cli import task_ml

        return task_ml()

    @task
    def dbt_post_ml(ml_result: dict) -> str:
        """Marty laczace fakty z prognoza i anomaliami."""
        from hallwatch_elt.cli import task_dbt

        task_dbt("build", "--select", "tag:post_ml")
        return "ok"

    @task
    def report(ml_result: dict, dbt_status: str) -> str:
        """Krotkie podsumowanie do logu - widac w UI bez wchodzenia w dane."""
        lines = [f"anomalie w oknie: {ml_result.get('anomalies', 0)}"]
        for m in ml_result.get("metrics", []):
            lines.append(
                f"{m['camera']}: MAE {m['mae_model']:.3f} vs naiwna "
                f"{m['mae_baseline']:.3f} ({m['improvement_pct']:+.1f}%)"
            )
        summary = " | ".join(lines)
        print(summary)
        return summary

    extracted = extract()
    loaded = load(extracted)
    core = dbt_core(loaded)
    ml = machine_learning(core)
    post = dbt_post_ml(ml)
    report(ml, post)


hallwatch_elt()
