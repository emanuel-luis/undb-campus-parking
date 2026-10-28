{{
    config(
        materialized='incremental',
        incremental_strategy='delete+insert',
        unique_key='event_id',
    )
}}

-- gate_events is append-only: rows received since the watermark (minus the lookback, for
-- transactions that committed late) are all an incremental run needs.
with events as (

    select * from {{ ref('stg_parking__gate_events') }}
    {% if is_incremental() %}
    where received_at > (
        select coalesce(max(received_at), timestamptz '-infinity') from {{ this }}
    ) - interval '{{ var("lookback_minutes") }} minutes'
    {% endif %}

),

local_events as (

    select
        events.*,
        {{ local_ts('events.occurred_at') }} as occurred_local

    from events

)

select
    local_events.event_id,
    local_events.service_date,
    {{ date_key('local_events.service_date') }} as date_key,
    cast(
        extract(hour from local_events.occurred_local) * 4
        + floor(extract(minute from local_events.occurred_local) / 15) as integer
    ) as slot_key,
    cast(extract(hour from local_events.occurred_local) as integer) as hour,
    local_events.lot_id,
    coalesce(lots.lot_key, {{ unknown_key() }}) as lot_key,
    local_events.gate_id,
    gates.gate_key,
    local_events.student_key,
    local_events.direction,
    local_events.occurred_at,
    local_events.received_at

from local_events
left join {{ ref('dim_lot') }} as lots
    on lots.lot_id = local_events.lot_id
    and local_events.service_date >= lots.valid_from
    and local_events.service_date < lots.valid_to
left join {{ ref('dim_gate') }} as gates
    on gates.gate_id = local_events.gate_id
