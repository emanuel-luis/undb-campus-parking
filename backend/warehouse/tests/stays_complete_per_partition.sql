-- Fails on every (lot, service date) where the incremental fct_stay does not hold exactly the
-- stays of the source, i.e. a partition that was missed, or left orphan rows behind.
-- The source is live (ingestion and close_day keep writing while the worker builds and tests),
-- so only partitions that cannot have moved since fct_stay was built are compared: dates
-- before today (local), with no source stay updated after the newest one in fct_stay.
with watermark as (

    select
        max(updated_at) as built_through,
        cast({{ local_ts(as_of()) }} as date) as today

    from {{ ref('fct_stay') }}

),

source as (

    select
        lot_id,
        service_date,
        count(*) as stays,
        count(distinct stay_id) as ids,
        max(updated_at) as last_update

    from {{ ref('stg_parking__stays') }}
    group by lot_id, service_date

),

fact as (

    select lot_id, service_date, count(*) as stays, count(distinct stay_id) as ids
    from {{ ref('fct_stay') }}
    group by lot_id, service_date

),

compared as (

    select
        coalesce(source.lot_id, fact.lot_id) as lot_id,
        coalesce(source.service_date, fact.service_date) as service_date,
        source.stays as source_stays,
        fact.stays as fact_stays,
        source.ids as source_ids,
        fact.ids as fact_ids,
        source.last_update

    from source
    full outer join fact
        on fact.lot_id = source.lot_id
        and fact.service_date = source.service_date

)

select compared.lot_id, compared.service_date, compared.source_stays, compared.fact_stays
from compared
cross join watermark
where compared.service_date < watermark.today
    and (compared.last_update is null or compared.last_update <= watermark.built_through)
    and (
        compared.source_stays is distinct from compared.fact_stays
        or compared.source_ids is distinct from compared.fact_ids
    )
