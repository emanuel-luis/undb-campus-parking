{{
    config(
        materialized='incremental',
        incremental_strategy='delete+insert',
        unique_key=['lot_id', 'service_date'],
    )
}}

-- A (lot, service date) partition is rebuilt whole when any of its stays changed since the
-- watermark: stays are deleted and re-inserted per student-day, so a stay can disappear.
with stays as (

    select * from {{ ref('int_stays_classified') }}

)

{% if is_incremental() %}
, changed as (

    select distinct lot_id, service_date
    from stays
    where updated_at > (
        select coalesce(max(updated_at), timestamptz '-infinity') from {{ this }}
    ) - interval '{{ var("lookback_minutes") }} minutes'

)
{% endif %}

select
    stays.stay_id,
    stays.lot_id,
    coalesce(lots.lot_key, {{ unknown_key() }}) as lot_key,
    stays.service_date,
    {{ date_key('stays.service_date') }} as date_key,
    stays.student_key,
    stays.status,
    stays.entered_at,
    stays.exited_at,
    stays.ended_at,
    stays.duration_s,
    stays.duration_bucket,
    stays.has_entry,
    stays.has_exit,
    stays.status = 'closed_by_reconciliation' as is_closed_by_reconciliation,
    stays.capacity,
    stays.updated_at

from stays
{% if is_incremental() %}
inner join changed
    on changed.lot_id = stays.lot_id
    and changed.service_date = stays.service_date
{% endif %}
left join {{ ref('dim_lot') }} as lots
    on lots.lot_id = stays.lot_id
    and stays.service_date >= lots.valid_from
    and stays.service_date < lots.valid_to
