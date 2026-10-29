-- One row per lot and local day that has occupancy snapshots (every day from the first stay to
-- today). Percentiles are per lot and day; they are never added across lots.
with lot_days as (

    select distinct lot_id, service_date, date_key, capacity
    from {{ ref('fct_occupancy_snapshot') }}

),

events as (

    select
        lot_id,
        service_date,
        count(*) filter (where direction = 'entry') as entries,
        count(*) filter (where direction = 'exit') as exits,
        count(distinct student_key) as unique_students

    from {{ ref('fct_gate_event') }}
    group by lot_id, service_date

),

stays as (

    select
        lot_id,
        service_date,
        count(*) filter (where has_entry) as stays,
        avg(duration_s) filter (where status = 'closed') / 60.0 as avg_stay_min,
        percentile_cont(0.5) within group (order by duration_s)
            filter (where status = 'closed') / 60.0 as median_stay_min,
        percentile_cont(0.9) within group (order by duration_s)
            filter (where status = 'closed') / 60.0 as p90_stay_min,
        count(*) filter (where status = 'closed_by_reconciliation') as closed_by_reconciliation,
        count(*) filter (where status = 'exit_only') as exit_only,
        count(*) filter (where status = 'missing_exit') as missing_exit

    from {{ ref('fct_stay') }}
    group by lot_id, service_date

),

ranked_slots as (

    select
        lot_id,
        service_date,
        slot_key,
        occupied_max,
        free_min,
        seconds_full,
        row_number() over (
            partition by lot_id, service_date order by occupied_max desc, slot_key
        ) as peak_rank

    from {{ ref('fct_occupancy_snapshot') }}

),

occupancy as (

    select
        ranked_slots.lot_id,
        ranked_slots.service_date,
        max(ranked_slots.occupied_max) as peak_occupied,
        max(times.slot_label) filter (where ranked_slots.peak_rank = 1) as peak_slot,
        min(ranked_slots.free_min) as min_free,
        sum(ranked_slots.seconds_full) / 60.0 as minutes_full

    from ranked_slots
    inner join {{ ref('dim_time') }} as times
        on times.slot_key = ranked_slots.slot_key
    group by ranked_slots.lot_id, ranked_slots.service_date

),

joined as (

    select
        lot_days.lot_id,
        lot_days.service_date,
        lot_days.date_key,
        lot_days.capacity,
        coalesce(events.entries, 0) as entries,
        coalesce(events.exits, 0) as exits,
        coalesce(events.unique_students, 0) as unique_students,
        stays.avg_stay_min,
        stays.median_stay_min,
        stays.p90_stay_min,
        occupancy.peak_occupied,
        occupancy.peak_slot,
        occupancy.min_free,
        occupancy.minutes_full,
        coalesce(stays.stays, 0) as stays,
        coalesce(stays.closed_by_reconciliation, 0) as closed_by_reconciliation,
        coalesce(stays.exit_only, 0) as exit_only,
        coalesce(stays.missing_exit, 0) as missing_exit

    from lot_days
    left join events
        on events.lot_id = lot_days.lot_id
        and events.service_date = lot_days.service_date
    left join stays
        on stays.lot_id = lot_days.lot_id
        and stays.service_date = lot_days.service_date
    left join occupancy
        on occupancy.lot_id = lot_days.lot_id
        and occupancy.service_date = lot_days.service_date

)

select
    cast(lot_id as text) as lot_id,
    cast(service_date as date) as service_date,
    cast(date_key as integer) as date_key,
    cast(capacity as integer) as capacity,
    cast(entries as integer) as entries,
    cast(exits as integer) as exits,
    cast(unique_students as integer) as unique_students,
    cast(round(avg(entries) over (
        partition by lot_id order by service_date
        range between interval '6 days' preceding and current row
    ), 2) as numeric(10, 2)) as entries_ma7,
    cast(round(cast(avg_stay_min as numeric), 2) as numeric(10, 2)) as avg_stay_min,
    cast(round(cast(median_stay_min as numeric), 2) as numeric(10, 2)) as median_stay_min,
    cast(round(cast(p90_stay_min as numeric), 2) as numeric(10, 2)) as p90_stay_min,
    cast(peak_occupied as integer) as peak_occupied,
    cast(peak_slot as text) as peak_slot,
    cast(min_free as integer) as min_free,
    cast(round(cast(minutes_full as numeric), 2) as numeric(10, 2)) as minutes_full,
    cast(stays as integer) as stays,
    cast(round(cast(stays as numeric) / nullif(capacity, 0), 2) as numeric(10, 2)) as turnover,
    cast(closed_by_reconciliation as integer) as closed_by_reconciliation,
    cast(exit_only as integer) as exit_only,
    cast(missing_exit as integer) as missing_exit

from joined
