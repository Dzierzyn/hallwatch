{{ config(materialized='table') }}

select
    traffic_date,
    camera,
    event_kind,
    sum(events)                                     as events,
    sum(crossings_total)                            as crossings_total,
    sum(count_in)                                   as count_in,
    sum(count_out)                                  as count_out,
    max(peak_concurrent)                            as peak_concurrent,
    sum(case when hour_of_day between 22 and 23
             or hour_of_day between 0 and 5
        then events else 0 end)                     as night_events,
    max(is_weekend)                                 as is_weekend
from {{ ref('agg_hourly_traffic') }}
group by 1, 2, 3
