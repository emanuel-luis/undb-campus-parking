-- Pseudonymous: the key is md5(student_ref); the ref itself never reaches the warehouse schema.
with seen as (

    select
        student_key,
        min(occurred_at) as first_seen_at,
        max(occurred_at) as last_seen_at

    from {{ ref('stg_parking__gate_events') }}
    group by student_key

)

select
    students.student_key,
    seen.first_seen_at,
    seen.last_seen_at

from {{ ref('stg_parking__students') }} as students
left join seen
    on seen.student_key = students.student_key
