# HallWatch Analytics — ELT + dbt + ML

Warstwa danych nad pipeline'em computer vision [HallWatch](../README.md).
Zdarzenia z kamer (osoby na korytarzu, pojazdy na ulicy) trafiają do hurtowni,
przechodzą modelowanie w dbt, a na końcu model prognozuje ruch i wykrywa anomalie.

```text
  pipeline CV                ELT                    hurtownia              ML
┌──────────────┐   ┌──────────────────┐   ┌────────────────────┐   ┌──────────────┐
│ SQLite       │──▶│ extract          │──▶│ dbt                │──▶│ prognoza 24h │
│ events       │   │ znacznik wodny   │   │ staging            │   │ anomalie     │
│ crossings    │   │ Parquet dt=...   │   │ intermediate       │   └──────┬───────┘
│ minute_stats │   └────────┬─────────┘   │ marts              │          │
└──────────────┘            │             └────────────────────┘          │
                            ▼                        ▲                    │
                   GCS + tabele zewnętrzne           └────────────────────┘
                   BigQuery (prod)                    mart_traffic_monitor
```

Orkiestracja: Airflow (`dags/hallwatch_elt_dag.py`).

## Dwa środowiska, jeden kod

| | `HW_TARGET=dev` | `HW_TARGET=prod` |
|---|---|---|
| Hurtownia | DuckDB na dysku | BigQuery |
| Surowe dane | Parquet czytany wprost | Parquet w GCS + tabele zewnętrzne |
| Koszt | zero | skan danych |
| Po co | rozwój i testy bez konta w chmurze | docelowe uruchomienie |

To nie jest wygoda, tylko warunek sensownej pracy: cały pipeline da się uruchomić
i przetestować lokalnie, a na BigQuery leci **ten sam kod**. Różnice dialektów SQL
są zamknięte w `dbt/macros/portable.sql`.

## Start

```bash
make install
make seed      # 90 dni syntetycznej historii z prawdziwą sezonowością
make all       # extract → dbt build → ML → dbt post_ml
```

Przejście na BigQuery:

```bash
cp .env.example .env && $EDITOR .env      # HW_GCP_PROJECT, HW_GCS_BUCKET
gcloud auth application-default login
set -a && . ./.env && set +a
make bq-setup
HW_TARGET=prod make all
```

## Decyzje projektowe

**Przyrost ze znacznikiem wodnym, nie pełny przeładunek.** Zdarzenia są
niezmienne po zamknięciu, a baza rośnie w nieskończoność. Zabieramy tylko to, co
przybyło, do partycji `dt=YYYY-MM-DD`. Nazwa pliku jest deterministyczna wobec
zakresu, więc powtórzony przebieg nadpisuje partycję zamiast dokleić wiersze.

**W BigQuery tabele zewnętrzne nad GCS, nie ładowanie do tabel natywnych.**
Idempotentność wychodzi z konstrukcji: retry Airflowa nadpisuje pliki, a nie
duplikuje danych — przy `WRITE_APPEND` duplikowałby, i to po cichu. Dodatkowo
układ jest symetryczny z dev, gdzie DuckDB czyta te same pliki.

**Strefa czasowa jawnie w obu dialektach.** DuckDB liczy `extract(hour)` w
strefie sesji, BigQuery w UTC. Bez makra `to_local()` ten sam kod dawałby **inny
profil dobowy** w dev i w prod, a wykryłby to dopiero ktoś, kto porównałby
wykresy. Analityka godzinowa liczy się w czasie lokalnym, bo „szczyt o 7 rano"
ma znaczyć 7 rano u mieszkańca.

**Kręgosłup czasu wypełnia zera.** Godzina bez ruchu to nie brak danych, to
informacja „zero". Bez `int_hour_spine` model nigdy nie zobaczyłby nocnej ciszy
i systematycznie zawyżałby prognozy.

**Prognoza bezpośrednia, nie rekurencyjna.** Model dostaje wyłącznie cechy znane
na 24 h przed prognozowaną godziną (opóźnienia ≥ 24 h). Nie podaje sobie własnych
predykcji na wejście, więc błąd się nie kumuluje, a ocena offline odpowiada temu,
co model zobaczy w produkcji.

**MAE, nie MAPE.** MAPE dzieli przez wartość rzeczywistą, a w nocy ruch wynosi
zero — procenty wybuchają i metryka kłamie. Każdy wynik jest raportowany wobec
naiwnej prognozy sezonowej („tyle co o tej porze tydzień temu"), bo bez punktu
odniesienia każda liczba MAE brzmi mądrze.

**Anomalie: mediana i MAD, nie średnia i odchylenie.** Anomalie są z definicji
wartościami skrajnymi — przy zwykłym odchyleniu standardowym same zawyżyłyby
skalę i schowały się przed detektorem.

## Wyniki na danych demo

```
korytarz   MAE 1.000 vs naiwna 1.188  -> +15.8% lepszy   (train=1655 test=336)
ulica      MAE 4.903 vs naiwna 6.286  -> +22.0% lepszy   (train=1655 test=336)
anomalie   38 z 672 godzin (5.65%)
dbt        PASS=32 (9 modeli + 23 testy)
```

Detektor anomalii odnajduje dokładnie te skoki, które generator wstrzyknął do
danych — to jest test poprawności, a nie tylko demonstracja.

## Testy dbt

23 testy: klucze główne, relacje `crossings → events`, słowniki wartości,
nieujemność zliczeń, unikalność ziarna martu godzinowego oraz test biznesowy
`assert_crossings_match_counts` — liczba zapisanych przekroczeń musi zgadzać się
z licznikami zdarzenia. Rozjazd oznacza, że pipeline CV zgubił zapis.

## Uwagi o środowisku

Airflow uruchamiaj przez `make airflow-up` (Docker). Lokalne wykonanie zadań
Airflow 3 na SQLite potrafi się zawiesić na macOS — DAG parsuje się poprawnie,
a wszystkie kroki są uruchamialne bez orkiestratora przez `hw-elt`, co jest
zresztą celowe: zadania Airflowa są cienkie i wołają te same funkcje co CLI.

## Struktura

```text
analytics/
  src/hallwatch_elt/
    config.py      ustawienia ze środowiska, ścieżki bezwzględne
    extract.py     SQLite → Parquet, znacznik wodny
    load.py        Parquet → GCS → tabele zewnętrzne BigQuery
    seed.py        generator syntetycznej historii
    warehouse.py   jeden interfejs do DuckDB i BigQuery
    ml/            cechy, prognoza, anomalie
    cli.py         hw-elt: te same kroki co w DAG-u
  dbt/
    macros/portable.sql   różnice dialektów w jednym miejscu
    models/staging|intermediate|marts
    tests/                testy biznesowe
  dags/hallwatch_elt_dag.py
  docker-compose.yaml     Airflow + Postgres
```
