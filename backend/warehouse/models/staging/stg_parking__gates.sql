select
    gate_id,
    lot_id,
    direction,
    name as gate_name

from {{ source('parking', 'gates') }}
