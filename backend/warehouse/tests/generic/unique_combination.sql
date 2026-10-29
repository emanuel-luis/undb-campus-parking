{# Fails on every combination of `columns` that appears more than once (no packages needed). #}
{% test unique_combination(model, columns) %}

select {{ columns | join(', ') }}, count(*) as occurrences
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1

{% endtest %}
