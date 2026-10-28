-- One row per stay with its duration bucket and the lot's capacity on the stay's service date.
with stays as (

    select * from {{ ref('stg_parking__stays') }}

),

capacity as (

    select
        lot_id,
        valid_from,
        coalesce(
            lead(valid_from) over (partition by lot_id order by valid_from),
            date '9999-12-31'
        ) as valid_to,
        capacity

    from {{ ref('stg_parking__lot_capacity_history') }}

)

select
    stays.stay_id,
    stays.lot_id,
    stays.student_key,
    stays.service_date,
    stays.status,
    stays.entered_at,
    stays.exited_at,
    stays.ended_at,
    stays.duration_s,
    case
        when stays.duration_s is null then null
        when stays.duration_s < 30 * 60 then '<30m'
        when stays.duration_s < 60 * 60 then '30-60m'
        when stays.duration_s < 2 * 3600 then '1-2h'
        when stays.duration_s < 4 * 3600 then '2-4h'
        when stays.duration_s < 6 * 3600 then '4-6h'
        else '>6h'
    end as duration_bucket,
    stays.entered_at is not null as has_entry,
    stays.exited_at is not null as has_exit,
    capacity.capacity,
    stays.updated_at

from stays
left join capacity
    on capacity.lot_id = stays.lot_id
    and stays.service_date >= capacity.valid_from
    and stays.service_date < capacity.valid_to
