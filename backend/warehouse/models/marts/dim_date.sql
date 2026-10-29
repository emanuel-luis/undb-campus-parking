with days as (

    select cast(day as date) as date_day
    from generate_series(
        cast('{{ var("dim_date_start") }}' as date),
        cast('{{ var("dim_date_end") }}' as date),
        interval '1 day'
    ) as day

)

select
    {{ date_key('days.date_day') }} as date_key,
    days.date_day,
    cast(extract(year from days.date_day) as integer) as year,
    cast(extract(month from days.date_day) as integer) as month,
    cast(extract(day from days.date_day) as integer) as day_of_month,
    cast(extract(isodow from days.date_day) as integer) as iso_day_of_week,
    (array[
        'segunda-feira', 'terça-feira', 'quarta-feira', 'quinta-feira', 'sexta-feira',
        'sábado', 'domingo'
    ])[cast(extract(isodow from days.date_day) as integer)] as weekday_name,
    extract(isodow from days.date_day) in (6, 7) as is_weekend,
    holidays.holiday_date is not null as is_holiday,
    holidays.holiday_name

from days
left join {{ ref('holidays') }} as holidays
    on holidays.holiday_date = days.date_day
