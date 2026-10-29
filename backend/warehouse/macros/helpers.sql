{#
    Surrogate key: md5 of the natural key columns joined with '|', NULL spelled '_null_'.
    Usage: {{ surrogate_key(['lot_id', 'valid_from']) }}
#}
{% macro surrogate_key(columns) -%}
    md5(
        {%- for column in columns %}
        coalesce(cast({{ column }} as text), '_null_'){% if not loop.last %} || '|' ||{% endif %}
        {%- endfor %}
    )
{%- endmacro %}

{# Key and id of the unknown member of dim_lot (rejects without a known lot). #}
{% macro unknown_key() -%}'-1'{%- endmacro %}

{# The instant the warehouse treats as now; unit tests override it. #}
{% macro as_of() -%}now(){%- endmacro %}

{# A timestamptz as local wall-clock time in the service time zone. #}
{% macro local_ts(column) -%}
    ({{ column }} at time zone '{{ var("timezone") }}')
{%- endmacro %}

{# YYYYMMDD integer key of a date (dim_date.date_key). #}
{% macro date_key(column) -%}
    cast(to_char({{ column }}, 'YYYYMMDD') as integer)
{%- endmacro %}
