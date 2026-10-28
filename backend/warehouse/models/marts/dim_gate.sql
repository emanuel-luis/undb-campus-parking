select
    {{ surrogate_key(['gate_id']) }} as gate_key,
    gate_id,
    lot_id,
    direction,
    gate_name

from {{ ref('stg_parking__gates') }}
