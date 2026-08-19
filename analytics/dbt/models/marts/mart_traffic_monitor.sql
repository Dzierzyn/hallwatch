{{ config(materialized='table', tags=['post_ml']) }}

-- Runs AFTER the ML layer (tag post_ml), because it reads its results.
-- Joins what happened with what we expected, and flags the mismatches.

with actuals as (
    select hour_ts, camera, event_kind, events, crossings_total,
           hour_of_day, day_of_week, is_weekend
    from {{ ref('agg_hourly_traffic') }}
),

fc as (
    select hour_ts, camera, predicted, baseline, is_forecast
    from {{ source('hallwatch_ml', 'ml_hourly_forecast') }}
),

an as (
    select hour_ts, camera, residual_z, is_anomaly, anomaly_reason
    from {{ source('hallwatch_ml', 'ml_hourly_anomaly') }}
)

select
    coalesce(a.hour_ts, f.hour_ts)      as hour_ts,
    coalesce(a.camera, f.camera)        as camera,
    a.event_kind,
    a.events                            as actual_events,
    f.predicted                         as predicted_events,
    f.baseline                          as baseline_events,
    coalesce(f.is_forecast, false)      as is_forecast,
    a.events - f.predicted              as residual,
    an.residual_z,
    coalesce(an.is_anomaly, false)      as is_anomaly,
    an.anomaly_reason,
    a.hour_of_day,
    a.day_of_week,
    a.is_weekend
from actuals a
full outer join fc f on f.hour_ts = a.hour_ts and f.camera = a.camera
left join an on an.hour_ts = coalesce(a.hour_ts, f.hour_ts)
            and an.camera = coalesce(a.camera, f.camera)
