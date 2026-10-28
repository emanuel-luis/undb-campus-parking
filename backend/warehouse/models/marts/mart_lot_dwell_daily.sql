-- One row per lot, local day and duration bucket (all six buckets, zero when empty). Closed
-- stays only: the other statuses have no measured duration.
with lot_days as (

    select distinct lot_id, service_date, date_key
    from {{ ref('fct_occupancy_snapshot') }}

),

buckets as (

    select * from (
        values
            ('<30m', 1), ('30-60m', 2), ('1-2h', 3), ('2-4h', 4), ('4-6h', 5), ('>6h', 6)
    ) as buckets (duration_bucket, bucket_order)

),

counts as (

    select lot_id, service_date, duration_bucket, count(*) as stays
    from {{ ref('fct_stay') }}
    where status = 'closed'
    group by lot_id, service_date, duration_bucket

)

select
    cast(lot_days.lot_id as text) as lot_id,
    cast(lot_days.service_date as date) as service_date,
    cast(lot_days.date_key as integer) as date_key,
    cast(buckets.duration_bucket as text) as duration_bucket,
    cast(buckets.bucket_order as integer) as bucket_order,
    cast(coalesce(counts.stays, 0) as integer) as stays

from lot_days
cross join buckets
left join counts
    on counts.lot_id = lot_days.lot_id
    and counts.service_date = lot_days.service_date
    and counts.duration_bucket = buckets.duration_bucket
