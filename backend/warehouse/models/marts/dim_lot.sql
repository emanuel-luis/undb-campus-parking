-- SCD2 on capacity, built from the effective-dated lot_capacity_history (business time).
with versions as (

    select
        history.lot_id,
        lots.lot_name,
        history.capacity,
        history.valid_from,
        coalesce(
            lead(history.valid_from) over (partition by history.lot_id order by history.valid_from),
            date '9999-12-31'
        ) as valid_to

    from {{ ref('stg_parking__lot_capacity_history') }} as history
    inner join {{ ref('stg_parking__lots') }} as lots
        on lots.lot_id = history.lot_id

)

select
    {{ surrogate_key(['lot_id', 'valid_from']) }} as lot_key,
    lot_id,
    lot_name,
    capacity,
    valid_from,
    valid_to,
    cast({{ local_ts(as_of()) }} as date) >= valid_from
        and cast({{ local_ts(as_of()) }} as date) < valid_to as is_current

from versions

union all

select
    {{ unknown_key() }} as lot_key,
    {{ unknown_key() }} as lot_id,
    'Desconhecido' as lot_name,
    cast(null as integer) as capacity,
    date '0001-01-01' as valid_from,
    date '9999-12-31' as valid_to,
    false as is_current
