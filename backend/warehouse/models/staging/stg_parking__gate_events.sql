select
    event_id,
    gate_id,
    lot_id,
    direction,
    md5(student_ref) as student_key,
    occurred_at,
    service_date,
    received_at,
    batch_id

from {{ source('parking', 'gate_events') }}
