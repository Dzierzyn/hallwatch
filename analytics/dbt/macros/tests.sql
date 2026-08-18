{% test dbt_utils_non_negative(model, column_name) %}
    {#- Wartosci zliczen i czasow trwania nie moga byc ujemne.
        Ujemna wartosc oznacza blad zegara albo uszkodzony zapis. -#}
    select {{ column_name }}
    from {{ model }}
    where {{ column_name }} < 0
{% endtest %}
