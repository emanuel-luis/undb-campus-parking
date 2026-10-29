select
    stay_id,
    lot_id,
    md5(student_ref) as student_key,
    service_date,
    entry_event_id,
    exit_event_id,
    entered_at,
    exited_at,
    ended_at,
    status,
    duration_s,
    updated_at

from {{ source('parking', 'stays') }}
