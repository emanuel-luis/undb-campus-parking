select
    lot_id,
    valid_from,
    capacity

from {{ source('parking', 'lot_capacity_history') }}
