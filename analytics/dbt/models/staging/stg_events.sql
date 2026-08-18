{{ config(materialized='view') }}

-- Oczyszczenie zdarzen: epoch -> timestamp, kamera wyciagnieta z JSON-a meta,
-- nazwy kolumn niezalezne od tego, czy liczylismy ludzi czy samochody.

with src as (
    select * from {{ source('hallwatch_raw', 'events') }}
)

select
    cast(id as bigint)                              as event_id,
    -- kamera jest kolumna od wersji wielokamerowej; starsze wiersze maja ja
    -- tylko w JSON-ie meta, wiec bierzemy pierwsza niepusta wartosc
    coalesce(nullif(camera, ''), {{ json_get('meta', 'camera') }}, 'nieznana') as camera,
    kind                                            as event_kind,
    {{ epoch_to_ts('started_at') }}                 as started_at,
    {{ to_local(epoch_to_ts('started_at')) }}       as started_at_local,
    {{ epoch_to_ts('ended_at') }}                   as ended_at,
    cast(ended_at - started_at as double)           as duration_s,
    cast(max_persons as bigint)                     as max_objects,
    cast(count_in as bigint)                        as count_in,
    cast(count_out as bigint)                       as count_out,
    cast(count_in as bigint) + cast(count_out as bigint) as crossings_total,
    peak_dbfs,
    clip_path is not null                           as has_clip,
    cloud_key is not null                           as in_cloud,
    coalesce(sampled, 0) = 1                        as is_sampled,
    coalesce(sample_weight, 1.0)                    as sample_weight
from src
where ended_at is not null
