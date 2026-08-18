-- Liczba zarejestrowanych przekroczen musi zgadzac sie z licznikami zdarzenia.
-- Rozjazd znaczy, ze pipeline CV zgubil zapis do jednej z tabel.
select
    e.event_id,
    e.crossings_total,
    count(c.crossing_id) as logged
from {{ ref('stg_events') }} e
left join {{ ref('stg_crossings') }} c on c.event_id = e.event_id
group by e.event_id, e.crossings_total
having count(c.crossing_id) <> e.crossings_total
