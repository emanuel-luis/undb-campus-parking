-- The pseudonymous ref never leaves staging: everything downstream sees its md5 only.
select
    md5(student_ref) as student_key,
    created_at

from {{ source('parking', 'students') }}
