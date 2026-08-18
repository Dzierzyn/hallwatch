-- Ziarno statystyk minutowych to para (kamera, minuta). Sama minuta nie jest
-- unikalna, odkad kamer jest wiecej niz jedna - i wlasnie dlatego ten test
-- zastapil prosty unique na kolumnie.
select camera, minute_epoch, count(*) as n
from {{ ref('stg_minute_stats') }}
group by 1, 2
having count(*) > 1
