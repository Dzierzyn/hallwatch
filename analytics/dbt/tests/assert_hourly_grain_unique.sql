-- The hourly mart's grain must be unique. A duplicate means a bug in the time
-- spine or in the join, and it would silently inflate every aggregation below.
select hour_ts, camera, event_kind, count(*) as n
from {{ ref('agg_hourly_traffic') }}
group by 1, 2, 3
having count(*) > 1
