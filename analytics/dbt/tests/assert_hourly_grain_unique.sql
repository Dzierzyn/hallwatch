-- Ziarno martu godzinowego musi byc unikalne. Duplikat oznacza blad w kregoslupie
-- czasu albo w joinie i cicho zawyzylby kazda agregacje ponizej.
select hour_ts, camera, event_kind, count(*) as n
from {{ ref('agg_hourly_traffic') }}
group by 1, 2, 3
having count(*) > 1
