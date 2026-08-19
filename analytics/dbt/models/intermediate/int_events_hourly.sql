{{ config(materialized='view') }}

select
    camera,
    event_kind,
    {{ trunc_hour('started_at_local') }}  as hour_ts,
    count(*)                        as events,
    -- sampling: we saw only part of the hour, so the observed count is not the
    -- real count. sample_weight = 1/duty_cycle is the extrapolation multiplier.
    -- For cameras observing continuously the weight is 1.
    sum(sample_weight)              as events_estimated,
    max(sample_weight)              as sample_weight,
    {{ any_true('is_sampled') }}    as is_sampled,
    sum(count_in)                   as count_in,
    sum(count_out)                  as count_out,
    sum(crossings_total)            as crossings_total,
    sum(crossings_total * sample_weight) as crossings_estimated,
    sum(max_objects)                as objects_seen,
    avg(duration_s)                 as avg_duration_s,
    max(max_objects)                as peak_concurrent
from {{ ref('stg_events') }}
group by 1, 2, 3
