-- Fails on any slot whose occupancy is negative or whose free spaces exceed the capacity.
select lot_id, service_date, slot_key, occupied_end, occupied_max, free_end, free_min, capacity
from {{ ref('fct_occupancy_snapshot') }}
where occupied_end < 0
    or occupied_max < 0
    or occupied_avg < 0
    or occupied_max < occupied_end
    or free_end > capacity
    or free_min > capacity
    or seconds_full < 0
