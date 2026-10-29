-- Fails on every lot-day where mart_lot_daily disagrees with fct_gate_event on entries or exits.
with facts as (

    select
        lot_id,
        service_date,
        count(*) filter (where direction = 'entry') as entries,
        count(*) filter (where direction = 'exit') as exits

    from {{ ref('fct_gate_event') }}
    group by lot_id, service_date

)

select facts.lot_id, facts.service_date, facts.entries, marts.entries as mart_entries
from facts
left join {{ ref('mart_lot_daily') }} as marts
    on marts.lot_id = facts.lot_id
    and marts.service_date = facts.service_date
where marts.entries is distinct from facts.entries
    or marts.exits is distinct from facts.exits
