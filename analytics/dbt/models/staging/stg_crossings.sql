{{ config(materialized='view') }}

with src as (
    select * from {{ source('hallwatch_raw', 'crossings') }}
)

select
    cast(id as bigint)              as crossing_id,
    cast(event_id as bigint)        as event_id,
    {{ epoch_to_ts('ts') }}         as crossed_at,
    cast(track_id as bigint)        as track_id,
    direction
from src
