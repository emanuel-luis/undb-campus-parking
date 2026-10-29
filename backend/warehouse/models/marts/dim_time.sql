-- 96 local 15-minute slots of a day; slot_key 0 is 00:00-00:15.
select
    slot as slot_key,
    cast(time '00:00' + slot * interval '15 minutes' as time) as slot_start,
    slot / 4 as hour,
    (slot % 4) * 15 as minute,
    to_char(time '00:00' + slot * interval '15 minutes', 'HH24:MI') as slot_label

from generate_series(0, 95) as slot
