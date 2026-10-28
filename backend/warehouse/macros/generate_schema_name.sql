{#
    Use the custom schema as-is (warehouse_staging) instead of dbt's default
    <target_schema>_<custom_schema>, so the names match docs/data-model.md.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
