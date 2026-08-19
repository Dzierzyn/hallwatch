-- The grain of minute stats is the (camera, minute) pair. The minute alone is
-- not unique once there is more than one camera - which is exactly why this
-- test replaced a plain unique on the column.
select camera, minute_epoch, count(*) as n
from {{ ref('stg_minute_stats') }}
group by 1, 2
having count(*) > 1
