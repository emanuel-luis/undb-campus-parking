{{
    config(
        materialized='incremental',
        incremental_strategy='delete+insert',
        unique_key=['lot_id', 'service_date'],
    )
}}

{#
    Occupancy per lot, local day and 15-minute slot, as a step function over stays: +1 at
    entered_at, -1 at ended_at (never for a stay still open). Stays without an entry
    (exit_only) never held a space we saw. Slots that have not started are not built; the
    current slot ends at now.

    Partitions rebuilt: the (lot, day) of every changed stay and the day after it (stays that
    cross midnight), plus yesterday and today for every lot; a full refresh builds every lot and
    day from the first stay to today.
#}

with clock as (

    select
        {{ as_of() }} as as_of,
        cast({{ local_ts(as_of()) }} as date) as today

),

stays as (

    select * from {{ ref('int_stays_classified') }}

),

lots as (

    select lot_id, lot_key, capacity, valid_from, valid_to
    from {{ ref('dim_lot') }}
    where lot_key <> {{ unknown_key() }}

),

{% if is_incremental() %}
changed as (

    select distinct lot_id, service_date
    from stays
    where updated_at > (
        select coalesce(max(built_at), timestamptz '-infinity') from {{ this }}
    ) - interval '{{ var("lookback_minutes") }} minutes'

),

partitions as (

    select lot_id, service_date from changed
    union
    select lot_id, service_date + 1 from changed
    union
    select lots.lot_id, clock.today - offsets.days
    from lots
    cross join clock
    cross join (values (0), (1)) as offsets (days)

),
{% else %}
partitions as (

    select lot_ids.lot_id, cast(days.day as date) as service_date
    from (select distinct lot_id from lots) as lot_ids
    cross join clock
    cross join generate_series(
        coalesce((select min(service_date) from stays), clock.today),
        clock.today,
        interval '1 day'
    ) as days (day)

),
{% endif %}

days as (

    select
        partitions.lot_id,
        partitions.service_date,
        lots.lot_key,
        lots.capacity

    from partitions
    cross join clock
    inner join lots
        on lots.lot_id = partitions.lot_id
        and partitions.service_date >= lots.valid_from
        and partitions.service_date < lots.valid_to
    where partitions.service_date <= clock.today

),

slots as (

    select
        days.lot_id,
        days.lot_key,
        days.capacity,
        days.service_date,
        times.slot_key,
        starts.slot_start,
        least(starts.slot_start + interval '15 minutes', clock.as_of) as slot_end

    from days
    cross join {{ ref('dim_time') }} as times
    cross join clock
    cross join lateral (
        select
            (days.service_date + times.slot_start) at time zone '{{ var("timezone") }}'
            as slot_start
    ) as starts
    where starts.slot_start < clock.as_of

),

windows as (

    select lot_id, min(slot_start) as starts_at, max(slot_end) as ends_at
    from slots
    group by lot_id

),

active_stays as (

    select
        stays.lot_id,
        stays.entered_at as starts_at,
        stays.ended_at as ends_at  -- NULL while the stay is open

    from stays
    inner join windows
        on windows.lot_id = stays.lot_id
    where stays.entered_at is not null
        and stays.entered_at < windows.ends_at
        and (stays.ended_at is null or stays.ended_at > windows.starts_at)

),

points as (

    select lot_id, starts_at as at, 1 as delta from active_stays
    union all
    select lot_id, ends_at as at, -1 as delta from active_stays where ends_at is not null
    union all
    select lot_id, slot_start as at, 0 as delta from slots
    union all
    select lot_id, slot_end as at, 0 as delta from slots

),

levels as (

    select
        lot_id,
        at,
        sum(sum(delta)) over (partition by lot_id order by at) as level,
        lead(at) over (partition by lot_id order by at) as next_at

    from points
    group by lot_id, at

),

-- Every slot boundary is a point, so the segment that starts at a point inside a slot ends
-- inside it (or at its end).
segments as (

    select
        slots.lot_id,
        slots.service_date,
        slots.slot_key,
        slots.capacity,
        levels.level,
        extract(epoch from least(levels.next_at, slots.slot_end) - levels.at) as seconds

    from slots
    inner join levels
        on levels.lot_id = slots.lot_id
        and cast({{ local_ts('levels.at') }} as date) = slots.service_date
        and cast(
            extract(hour from {{ local_ts('levels.at') }}) * 4
            + floor(extract(minute from {{ local_ts('levels.at') }}) / 15) as integer
        ) = slots.slot_key
        and levels.at < slots.slot_end

),

per_slot as (

    select
        lot_id,
        service_date,
        slot_key,
        max(level) as occupied_max,
        sum(level * seconds) / nullif(sum(seconds), 0) as occupied_avg,
        sum(case when level >= capacity then seconds else 0 end) as seconds_full

    from segments
    group by lot_id, service_date, slot_key

)

select
    slots.lot_id,
    slots.lot_key,
    slots.service_date,
    {{ date_key('slots.service_date') }} as date_key,
    slots.slot_key,
    slots.slot_start,
    cast(ends.level as integer) as occupied_end,
    cast(per_slot.occupied_max as integer) as occupied_max,
    cast(round(cast(per_slot.occupied_avg as numeric), 2) as numeric(10, 2)) as occupied_avg,
    slots.capacity,
    cast(greatest(slots.capacity - ends.level, 0) as integer) as free_end,
    cast(greatest(slots.capacity - per_slot.occupied_max, 0) as integer) as free_min,
    cast(round(per_slot.seconds_full) as integer) as seconds_full,
    clock.as_of as built_at

from slots
cross join clock
inner join per_slot
    on per_slot.lot_id = slots.lot_id
    and per_slot.service_date = slots.service_date
    and per_slot.slot_key = slots.slot_key
inner join levels as ends
    on ends.lot_id = slots.lot_id
    and ends.at = slots.slot_end
