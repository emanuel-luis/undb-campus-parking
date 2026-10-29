-- detail and payload stay in the OLTP table: reports need counts only.
select
    reject_id,
    batch_id,
    received_at,
    cast({{ local_ts('received_at') }} as date) as received_date,
    event_id,
    service_date,
    lot_id,
    gate_id,
    reason

from {{ source('parking', 'ingest_rejects') }}
