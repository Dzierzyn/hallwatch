{#
  Makra przenosnosci miedzy DuckDB (dev) i BigQuery (prod).

  Powod istnienia: chcemy rozwijac i testowac caly pipeline lokalnie, bez konta
  w chmurze i bez placenia za kazde 'dbt run', a jednoczesnie uruchamiac ten SAM
  kod na BigQuery. Roznice dialektow sa zamkniete tutaj, dzieki czemu modele
  pozostaja czytelnym SQL-em.
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
  {#- ujednolicone do 1=niedziela .. 7=sobota (konwencja BigQuery) -#}
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
    Strefa czasowa jawnie, bo domyslne zachowanie sie ROZNI:
    DuckDB liczy extract() w strefie sesji, BigQuery w UTC. Bez tego makra
    ten sam kod dawalby inny profil dobowy w dev i w prod, i nikt by tego
    nie zauwazyl, dopoki ktos nie porownalby wykresow.
    Obie galezie zwracaja czas LOKALNY bez strefy.
  -#}
  {%- if target.type == 'bigquery' -%}
    datetime({{ col }}, '{{ var("timezone") }}')
  {%- else -%}
    timezone('{{ var("timezone") }}', {{ col }})
  {%- endif -%}
{%- endmacro %}
