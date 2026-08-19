"""DAG: HallWatch ELT + ML.

The shape of the flow follows from a single dependency: the ML models read dbt
marts, and the monitoring mart reads the ML results. Hence two dbt runs separated
by the ML step, otherwise the graph would contain a cycle.

    extract -> load -> dbt build (without post_ml) -> ml -> dbt build (post_ml)

The tasks are thin: they call the same functions as the CLI. All the logic lives
in the hallwatch_elt package, so it can be run and debugged without Airflow.
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
    description="Camera events -> BigQuery -> dbt -> forecast and anomalies",
    schedule="15 * * * *",  # kwadrans po kazdej pelnej godzinie
    start_date=pendulum.datetime(2026, 1, 1, tz="Europe/Warsaw"),
    catchup=False,
    max_active_runs=1,  # watermark-based increments do not tolerate parallelism
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
        """Staging, intermediate and marts, everything that does not depend on ML."""
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
        """A short summary for the log, visible in the UI without digging into the data."""
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
