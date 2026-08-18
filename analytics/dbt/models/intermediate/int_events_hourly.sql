{{ config(materialized='view') }}

select
    camera,
    event_kind,
    {{ trunc_hour('started_at_local') }}  as hour_ts,
    count(*)                        as events,
    sum(count_in)                   as count_in,
    sum(count_out)                  as count_out,
    sum(crossings_total)            as crossings_total,
    sum(max_objects)                as objects_seen,
    avg(duration_s)                 as avg_duration_s,
    max(max_objects)                as peak_concurrent
from {{ ref('stg_events') }}
group by 1, 2, 3
