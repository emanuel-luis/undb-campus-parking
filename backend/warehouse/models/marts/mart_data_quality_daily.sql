-- One row per local day and lot: every lot-day with snapshots, plus every (day, lot) with a
-- reject. A reject whose lot is missing or not registered goes to the unknown lot '-1'; its day
-- is the event's service date, or the local date it was received when it has none.
with lots as (

    select lot_id from {{ ref('stg_parking__lots') }}

),

rejects as (

    select
        coalesce(rejects.service_date, rejects.received_date) as service_date,
        coalesce(lots.lot_id, {{ unknown_key() }}) as lot_id,
        rejects.reason

    from {{ ref('stg_parking__ingest_rejects') }} as rejects
    left join lots
        on lots.lot_id = rejects.lot_id

),

reject_counts as (

    select
        service_date,
        lot_id,
        count(*) as rejects_total,
        count(*) filter (where reason = 'schema_invalid') as rejects_schema_invalid,
        count(*) filter (where reason = 'unknown_gate') as rejects_unknown_gate,
        count(*) filter (where reason = 'unknown_student') as rejects_unknown_student,
        count(*) filter (where reason = 'lot_mismatch') as rejects_lot_mismatch,
        count(*) filter (where reason = 'direction_mismatch') as rejects_direction_mismatch,
        count(*) filter (where reason = 'in_future') as rejects_in_future,
        count(*) filter (where reason = 'too_old') as rejects_too_old,
        count(*) filter (where reason = 'event_id_conflict') as rejects_event_id_conflict

    from rejects
    group by service_date, lot_id

),

stay_counts as (

    select
        service_date,
        lot_id,
        count(*) filter (where status = 'closed_by_reconciliation') as closed_by_reconciliation,
        count(*) filter (where status = 'exit_only') as exit_only,
        count(*) filter (where status = 'missing_exit') as missing_exit

    from {{ ref('fct_stay') }}
    group by service_date, lot_id

),

spine as (

    select distinct service_date, lot_id from {{ ref('fct_occupancy_snapshot') }}
    union
    select service_date, lot_id from reject_counts

)

select
    cast(spine.service_date as date) as service_date,
    cast({{ date_key('spine.service_date') }} as integer) as date_key,
    cast(spine.lot_id as text) as lot_id,
    cast(coalesce(reject_counts.rejects_total, 0) as integer) as rejects_total,
    cast(coalesce(reject_counts.rejects_schema_invalid, 0) as integer) as rejects_schema_invalid,
    cast(coalesce(reject_counts.rejects_unknown_gate, 0) as integer) as rejects_unknown_gate,
    cast(coalesce(reject_counts.rejects_unknown_student, 0) as integer)
        as rejects_unknown_student,
    cast(coalesce(reject_counts.rejects_lot_mismatch, 0) as integer) as rejects_lot_mismatch,
    cast(coalesce(reject_counts.rejects_direction_mismatch, 0) as integer)
        as rejects_direction_mismatch,
    cast(coalesce(reject_counts.rejects_in_future, 0) as integer) as rejects_in_future,
    cast(coalesce(reject_counts.rejects_too_old, 0) as integer) as rejects_too_old,
    cast(coalesce(reject_counts.rejects_event_id_conflict, 0) as integer)
        as rejects_event_id_conflict,
    cast(coalesce(stay_counts.closed_by_reconciliation, 0) as integer)
        as closed_by_reconciliation,
    cast(coalesce(stay_counts.exit_only, 0) as integer) as exit_only,
    cast(coalesce(stay_counts.missing_exit, 0) as integer) as missing_exit

from spine
left join reject_counts
    on reject_counts.service_date = spine.service_date
    and reject_counts.lot_id = spine.lot_id
left join stay_counts
    on stay_counts.service_date = spine.service_date
    and stay_counts.lot_id = spine.lot_id
