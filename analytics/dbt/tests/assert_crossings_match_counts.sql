-- The number of recorded crossings must agree with the event counters.
-- A mismatch means the CV pipeline dropped a write to one of the tables.
select
    e.event_id,
    e.crossings_total,
    count(c.crossing_id) as logged
from {{ ref('stg_events') }} e
left join {{ ref('stg_crossings') }} c on c.event_id = e.event_id
group by e.event_id, e.crossings_total
having count(c.crossing_id) <> e.crossings_total
