{{ config(materialized='table') }}

select
    e.event_id,
    e.camera,
    e.event_kind,
    e.started_at,
    e.ended_at,
    e.duration_s,
    e.max_objects,
    e.count_in,
    e.count_out,
    e.crossings_total,
    e.peak_dbfs,
    e.has_clip,
    e.in_cloud,
    cast(e.started_at_local as date) as event_date,
    extract(hour from e.started_at_local) as hour_of_day,
    {{ day_of_week('e.started_at_local') }} as day_of_week,
    count(c.crossing_id)            as crossings_logged
from {{ ref('stg_events') }} e
left join {{ ref('stg_crossings') }} c on c.event_id = e.event_id
group by
    e.event_id, e.camera, e.event_kind, e.started_at, e.started_at_local, e.ended_at, e.duration_s,
    e.max_objects, e.count_in, e.count_out, e.crossings_total, e.peak_dbfs,
    e.has_clip, e.in_cloud
