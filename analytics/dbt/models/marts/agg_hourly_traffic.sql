{{ config(materialized='table') }}

-- Grain: hour x camera. Input table for ML and for the dashboard.

with cameras as (
    -- Sampling is a property of the CAMERA, not of a single hour. If the weight
    -- were taken from the row, hours with no observed traffic would get weight 1
    -- and drop out of the extrapolation, systematically overstating the average
    -- intensity.
    select
        camera,
        event_kind,
        max(sample_weight)            as sample_weight,
        {{ any_true('is_sampled') }}  as is_sampled
    from {{ ref('int_events_hourly') }}
    group by 1, 2
),

grid as (
    select s.hour_ts, c.camera, c.event_kind, c.sample_weight, c.is_sampled
    from {{ ref('int_hour_spine') }} s
    cross join cameras c
),

joined as (
    select
        g.hour_ts,
        g.camera,
        g.event_kind,
        g.sample_weight,
        g.is_sampled,
        coalesce(a.events, 0)                        as events,
        coalesce(a.events, 0) * g.sample_weight      as events_estimated,
        coalesce(a.count_in, 0)                      as count_in,
        coalesce(a.count_out, 0)                     as count_out,
        coalesce(a.crossings_total, 0)               as crossings_total,
        coalesce(a.crossings_total, 0) * g.sample_weight as crossings_estimated,
        coalesce(a.objects_seen, 0)                  as objects_seen,
        a.avg_duration_s,
        coalesce(a.peak_concurrent, 0)               as peak_concurrent
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
