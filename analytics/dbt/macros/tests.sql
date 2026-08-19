{% test dbt_utils_non_negative(model, column_name) %}
    {#- Counts and durations must not be negative.
        A negative value means a clock error or a corrupted record. -#}
    select {{ column_name }}
    from {{ model }}
    where {{ column_name }} < 0
{% endtest %}
