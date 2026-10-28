select
    lot_id,
    name as lot_name

from {{ source('parking', 'lots') }}
