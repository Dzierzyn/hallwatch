{{ config(materialized='view') }}

with src as (
    select * from {{ source('hallwatch_raw', 'minute_stats') }}
)

select
    coalesce(nullif(camera, ''), 'unknown') as camera,
    cast(minute as bigint)              as minute_epoch,
    {{ epoch_min_to_ts('minute') }}     as minute_ts,
    cast(frames as bigint)              as frames,
    cast(motion as bigint)              as motion_frames,
    cast(persons as bigint)             as peak_objects,
    cast(count_in as bigint)            as count_in,
    cast(count_out as bigint)           as count_out
from src
