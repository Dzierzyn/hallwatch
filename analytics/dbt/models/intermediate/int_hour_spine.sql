{{ config(materialized='view') }}

-- Time spine: EVERY hour in the data range, including those with zero traffic.
-- Without it the forecasting model would never see the quiet of the night and
-- would overstate its predictions - a missing row is not missing data, it is
-- the information "zero".

with bounds as (
    select
        min({{ trunc_hour('started_at_local') }}) as lo,
        max({{ trunc_hour('started_at_local') }}) as hi
    from {{ ref('stg_events') }}
)

{{ hour_spine('(select lo from bounds)', '(select hi from bounds)') }}
