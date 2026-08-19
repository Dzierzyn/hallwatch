{#
  Portability macros between DuckDB (dev) and BigQuery (prod).

  Reason to exist: we want to develop and test the whole pipeline locally,
  without a cloud account and without paying for every 'dbt run', while running
  the SAME code on BigQuery. Dialect differences are confined here, so the
  models stay readable SQL.
#}

{% macro epoch_to_ts(col) -%}
  {%- if target.type == 'bigquery' -%}
    timestamp_seconds(cast({{ col }} as int64))
  {%- else -%}
    to_timestamp({{ col }})
  {%- endif -%}
{%- endmacro %}

{% macro epoch_min_to_ts(col) -%}
  {%- if target.type == 'bigquery' -%}
    timestamp_seconds(cast({{ col }} as int64) * 60)
  {%- else -%}
    to_timestamp({{ col }} * 60)
  {%- endif -%}
{%- endmacro %}

{% macro trunc_hour(col) -%}
  {%- if target.type == 'bigquery' -%}
    timestamp_trunc({{ col }}, hour)
  {%- else -%}
    date_trunc('hour', {{ col }})
  {%- endif -%}
{%- endmacro %}

{% macro day_of_week(col) -%}
  {#- unified to 1=Sunday .. 7=Saturday (BigQuery convention) -#}
  {%- if target.type == 'bigquery' -%}
    extract(dayofweek from {{ col }})
  {%- else -%}
    cast(extract(dow from {{ col }}) as int) + 1
  {%- endif -%}
{%- endmacro %}

{% macro json_get(col, key) -%}
  {%- if target.type == 'bigquery' -%}
    json_value({{ col }}, '$.{{ key }}')
  {%- else -%}
    json_extract_string({{ col }}, '$.{{ key }}')
  {%- endif -%}
{%- endmacro %}

{% macro hour_spine(start_expr, end_expr) -%}
  {%- if target.type == 'bigquery' -%}
    select h as hour_ts
    from unnest(generate_timestamp_array({{ start_expr }}, {{ end_expr }}, interval 1 hour)) as h
  {%- else -%}
    select unnest(generate_series({{ start_expr }}, {{ end_expr }}, interval 1 hour)) as hour_ts
  {%- endif -%}
{%- endmacro %}

{% macro to_local(col) -%}
  {#-
    Time zone stated explicitly, because the default behaviour DIFFERS:
    DuckDB computes extract() in the session zone, BigQuery in UTC. Without
    this macro the same code would give a different daily profile in dev and
    in prod, and nobody would notice until someone compared the charts.
    Both branches return LOCAL time without a zone.
  -#}
  {%- if target.type == 'bigquery' -%}
    datetime({{ col }}, '{{ var("timezone") }}')
  {%- else -%}
    timezone('{{ var("timezone") }}', {{ col }})
  {%- endif -%}
{%- endmacro %}

{% macro any_true(col) -%}
  {#- DuckDB has bool_or, BigQuery uses logical_or -#}
  {%- if target.type == 'bigquery' -%}
    logical_or({{ col }})
  {%- else -%}
    bool_or({{ col }})
  {%- endif -%}
{%- endmacro %}
