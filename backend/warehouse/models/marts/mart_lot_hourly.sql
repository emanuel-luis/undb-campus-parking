-- One row per lot, local day and hour that has started.
with hours as (

    select
        lot_id,
        service_date,
        date_key,
        slot_key / 4 as hour,
        max(capacity) as capacity,
        avg(greatest(capacity - occupied_avg, 0)) as avg_free,
        avg(occupied_avg) as avg_occupied,
        max(occupied_max) as max_occupied

    from {{ ref('fct_occupancy_snapshot') }}
    group by lot_id, service_date, date_key, slot_key / 4

),

events as (

    select
        lot_id,
        service_date,
        hour,
        count(*) filter (where direction = 'entry') as entries,
        count(*) filter (where direction = 'exit') as exits

    from {{ ref('fct_gate_event') }}
    group by lot_id, service_date, hour

)

select
    cast(hours.lot_id as text) as lot_id,
    cast(hours.service_date as date) as service_date,
    cast(hours.date_key as integer) as date_key,
    cast(hours.hour as integer) as hour,
    cast(hours.capacity as integer) as capacity,
    cast(coalesce(events.entries, 0) as integer) as entries,
    cast(coalesce(events.exits, 0) as integer) as exits,
    cast(round(hours.avg_free, 2) as numeric(10, 2)) as avg_free,
    cast(round(hours.avg_occupied, 2) as numeric(10, 2)) as avg_occupied,
    cast(hours.max_occupied as integer) as max_occupied

from hours
left join events
    on events.lot_id = hours.lot_id
    and events.service_date = hours.service_date
    and events.hour = hours.hour
