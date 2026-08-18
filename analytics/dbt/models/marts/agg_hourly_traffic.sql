{{ config(materialized='table') }}

-- Ziarno: godzina x kamera. To jest tabela wejsciowa dla ML i dla dashboardu.

with cameras as (
    select distinct camera, event_kind from {{ ref('int_events_hourly') }}
),

grid as (
    select s.hour_ts, c.camera, c.event_kind
    from {{ ref('int_hour_spine') }} s
    cross join cameras c
),

joined as (
    select
        g.hour_ts,
        g.camera,
        g.event_kind,
        coalesce(a.events, 0)           as events,
        coalesce(a.count_in, 0)         as count_in,
        coalesce(a.count_out, 0)        as count_out,
        coalesce(a.crossings_total, 0)  as crossings_total,
        coalesce(a.objects_seen, 0)     as objects_seen,
        a.avg_duration_s,
        coalesce(a.peak_concurrent, 0)  as peak_concurrent
    from grid g
    left join {{ ref('int_events_hourly') }} a
        on a.hour_ts = g.hour_ts and a.camera = g.camera and a.event_kind = g.event_kind
)

select
    *,
    cast(hour_ts as date)                   as traffic_date,
    extract(hour from hour_ts)              as hour_of_day,
    {{ day_of_week('hour_ts') }}            as day_of_week,
    {{ day_of_week('hour_ts') }} in (1, 7)  as is_weekend
from joined
