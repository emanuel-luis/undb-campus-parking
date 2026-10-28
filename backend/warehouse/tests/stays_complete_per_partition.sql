-- Fails on every (lot, service date) where the incremental fct_stay does not hold exactly the
-- stays of the source, i.e. a partition that was missed, or left orphan rows behind.
with source as (

    select lot_id, service_date, count(*) as stays, count(distinct stay_id) as ids
    from {{ ref('stg_parking__stays') }}
    group by lot_id, service_date

),

fact as (

    select lot_id, service_date, count(*) as stays, count(distinct stay_id) as ids
    from {{ ref('fct_stay') }}
    group by lot_id, service_date

)

select
    coalesce(source.lot_id, fact.lot_id) as lot_id,
    coalesce(source.service_date, fact.service_date) as service_date,
    source.stays as source_stays,
    fact.stays as fact_stays

from source
full outer join fact
    on fact.lot_id = source.lot_id
    and fact.service_date = source.service_date
where source.stays is distinct from fact.stays
    or source.ids is distinct from fact.ids
