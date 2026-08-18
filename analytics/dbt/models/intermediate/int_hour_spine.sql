{{ config(materialized='view') }}

-- Kregoslup czasu: KAZDA godzina w zakresie danych, takze te z zerowym ruchem.
-- Bez tego model prognozujacy nigdy nie zobaczy nocnej ciszy i bedzie zawyzal
-- przewidywania - brak wiersza to nie brak danych, to informacja "zero".

with bounds as (
    select
        min({{ trunc_hour('started_at_local') }}) as lo,
        max({{ trunc_hour('started_at_local') }}) as hi
    from {{ ref('stg_events') }}
)

{{ hour_spine('(select lo from bounds)', '(select hi from bounds)') }}
