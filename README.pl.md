# HallWatch

System computer vision do monitoringu korytarza: detekcja ruchu, wykrywanie
i **liczenie osób** z trackingiem, detekcja dźwięku, nagrywanie zdarzeń
z pre-rollem, backup do chmury i dashboard webowy w czasie rzeczywistym.

Zbudowany wokół jednej zasady: **maska prywatności jest pierwszym krokiem
pipeline'u**, więc obszary, które nie należą do właściciela systemu, nie trafiają
ani do detekcji, ani na dysk, ani do chmury.

```text
┌────────┐   ┌──────────┐   ┌────────┐   ┌─────────────┐   ┌──────────┐
│ kamera │──▶│  MASKA   │──▶│ MOG2   │──▶│ YOLO11 +    │──▶│ licznik  │
│ RTSP   │   │PRYWATNO- │   │ ruch   │   │ ByteTrack   │   │ linii    │
└────────┘   │  ŚCI     │   │(~1 ms) │   │ (~16 ms/MPS)│   │ i stref  │
             └──────────┘   └────┬───┘   └─────────────┘   └────┬─────┘
                                 │ brak ruchu → YOLO śpi        │
                                 ▼                              ▼
        ┌────────────────────────────────────────────────────────────┐
        │  bufor pierścieniowy (pre-roll) → klip H.264 → SQLite      │
        │  → upload S3 → push ntfy → dashboard MJPEG                 │
        └────────────────────────────────────────────────────────────┘
```

## Szybki start

```bash
make install    # venv + zależności + obejście problemu .pth (patrz niżej)

# 1. sprawdź, czy wszystko działa — bez kamery, na syntetycznym wideo
make test

# 2. znajdź kamerę w sieci lokalnej
hallwatch scan

# 3. sprawdź strumień: rozdzielczość, realny FPS, obecność audio
hallwatch probe --source 'rtsp://user:haslo@192.168.1.50:554/h264Preview_01_main'

# 4. wyznacz klikaniem linię zliczającą, strefy i maski prywatności
hallwatch zones --source 'rtsp://...'

# 5. uruchom
hallwatch run          # dashboard: http://127.0.0.1:8000
```

Nie masz jeszcze kamery? Wszystko działa na wbudowanej kamerze Maca
(`source: "0"`) albo na pliku `.mp4` — pełny pipeline można rozwijać offline.

### Znany problem: macOS + uv + Python ≥ 3.12

`uv pip install -e .` kończy się sukcesem, a `import hallwatch` mimo to rzuca
`ModuleNotFoundError`. Przyczyna: uv ustawia na plikach `.pth` macOS-ową flagę
`UF_HIDDEN`, a `site.py` od Pythona 3.12 **celowo pomija ukryte `.pth`** — więc
ścieżka z editable install nigdy nie trafia do `sys.path`. Diagnoza:

```bash
ls -lO .venv/lib/python3.12/site-packages/*.pth   # kolumna flag pokaże "hidden"
```

`make install` rozwiązuje to dwutorowo: zdejmuje flagę (`make fix-pth`) i
dodatkowo ustawia `PYTHONPATH=src`, więc działa nawet gdy flaga wróci.

## Wiele kamer, trzy tryby pracy

`config.yaml` opisuje `defaults` i liste `cameras`; kazda kamera nadpisuje tylko
to, czym naprawde sie rozni. Jeden proces obsluguje wszystkie, ze **wspolna baza,
kolejka uploadu i powiadomieniami** — zdarzenia z roznych kamer musza lezec na
jednej osi czasu, inaczej warstwa analityczna nie zlaczy ich w jeden obraz.

| Tryb | Dla kogo | Zachowanie |
| --- | --- | --- |
| `continuous` | kamera z zasilaniem | strumien otwarty non stop |
| `on_demand` | kamera na baterii | laczy sie na sygnal, potem pozwala zasnac |
| `sampling` | statystyki ruchu | obserwuje okno co ustalony czas i ekstrapoluje |

Przyklad z repo: korytarz liczy osoby w `on_demand`, ulica liczy pojazdy
(`classes: [2,3,5,7]`) w `sampling`, bez nagrywania. Dashboard ma przelacznik
kamer, a kazdy endpoint przyjmuje `?camera=<slug>`.

**Dlaczego ulica nie moze dzialac na detekcji ruchu.** Wyzwalacz PIR przegapilby
wiekszosc samochodow i nie wiadomo ktore — liczby nie znaczylyby nic. Probkowanie
jest uczciwe: wiemy dokladnie, jaka czesc czasu widzielismy, kazde zdarzenie
niesie mnoznik `1/duty_cycle`, a analityka odtwarza natezenie. Na danych demo
zapisanych z 1/12 ruchu ekstrapolacja daje 19,6 zdarzen/h przy prawdzie ~19.

Stary jednokamerowy `config.yaml` nadal sie wczytuje — jest zawijany w liste
jednoelementowa, wiec aktualizacja nie wymaga przepisywania pliku.

## Kamera na baterii (tryb `on_demand`)

Kamery bateryjne **nie udostępniają RTSP** — ani Reolink, ani Tapo; producenci
blokują to wprost z powodu zużycia energii. Wyjątkiem jest Reolink z **Home
Hubem**, który wystawia RTSP w imieniu kamery, bez abonamentu.

Zostaje jednak fizyka: utrzymywanie otwartego strumienia nie pozwala kamerze
zasnąć i zjada akumulator w kilka dni. Dlatego `camera.mode: on_demand` nie
trzyma połączenia — czeka na sygnał, łapie krótką sesję i rozłącza się, by
kamera wróciła do snu:

```bash
curl -X POST http://127.0.0.1:8000/api/wake    # webhook: hub, Home Assistant, cokolwiek
make wake                                       # albo z terminala
```

Sesja kończy się po `session_idle_s` ciszy lub po `session_seconds` twardego
limitu. `active_hours` (np. `"07:00-23:00"`) pozwala ignorować sygnały nocą.

**Podgląd live nie znika w tym trybie** — jest dostępny w każdej sesji, a przycisk
„Podgląd na żądanie" w dashboardzie budzi kamerę i trzyma sesję otwartą, dopóki
patrzysz, nawet gdy w kadrze nic się nie rusza. Przeglądarka wysyła puls
`POST /api/watch` co 10 s; ukrycie karty przestaje go wysyłać, więc kamera
zasypia sama. `watch_max_s` jest bezpiecznikiem na zapomnianą zakładkę —
inaczej jedno otwarte okno rozładowałoby akumulator.
Model tła MOG2 jest budowany od nowa na każdą sesję ze skróconym warmupem — po
przerwie stary model jest bezużyteczny, a o zdarzeniu i tak wiemy z sygnału.

## Co robi

| Funkcja | Realizacja |
| --- | --- |
| Detekcja ruchu | odejmowanie tła MOG2 na obrazie 1/2, próg na ułamek powierzchni kadru |
| Detekcja osób | YOLO11 (Ultralytics), akceleracja Apple MPS / CUDA / CPU |
| Tracking | ByteTrack — trwałe `track_id` między klatkami |
| Liczenie przejść | zmiana znaku iloczynu wektorowego względem odcinka + kontrola, czy przecięcie leży w jego obrębie |
| Obecność w strefach | test punkt-w-wielokącie dla kotwicy (`feet` / `center`) |
| Detekcja dźwięku | osobny proces `ffmpeg -vn` → PCM → dBFS z histerezą |
| Nagrywanie | bufor pierścieniowy pre-roll → pipe do `ffmpeg` → H.264 mp4 |
| Zdarzenia | SQLite (WAL): zdarzenia, przejścia, agregaty minutowe |
| Chmura | dowolny storage S3-compatible: Cloudflare R2, Backblaze B2, MinIO, AWS |
| Powiadomienia | ntfy.sh z miniaturą zdarzenia, z throttlingiem |
| Dashboard | FastAPI + MJPEG, licznik live, timeline zdarzeń z odtwarzaczem klipów |
| Retencja | `hallwatch prune` usuwa media starsze niż `retention_days` |

## Decyzje projektowe

**MOG2 jako bramka przed YOLO.** Korytarz jest pusty przez większość doby.
Uruchamianie sieci neuronowej 15 razy na sekundę przez 24 h to marnowanie prądu
bez zysku informacyjnego. MOG2 kosztuje ~1 ms i decyduje, kiedy wybudzić
detektor na `detection.awake_seconds`. W praktyce YOLO pracuje kilka procent
czasu.

**Kotwica zależna od kąta montażu** (`counting.anchor`). O przekroczeniu linii
decyduje jeden punkt sylwetki — i jego wybór zależy od tego, skąd patrzy kamera.
Przy ujęciu z boku lub skosem w dół korytarza właściwe są **stopy** (`feet`):
trzymają się podłogi, a to podłoga przecina linię; środek boxa skakałby, gdy
ktoś podnosi rękę albo częściowo wychodzi z kadru. Przy kamerze pod sufitem
patrzącej **prosto w dół** stóp nie ma — sylwetka jest plamą, której dolna
krawędź zmienia się z każdym krokiem — więc jedynym stabilnym punktem jest
**środek** (`center`). Oba tryby mają regresję na dokładnie jedno przejście.

Widok z góry ma jeszcze jeden skutek: COCO uczono głównie na ujęciach z boku,
więc pewność detekcji spada. Zapas jest w `detection.conf` (0.25–0.30) i w
mocniejszym modelu `yolo11s.pt`.

**Kontrola odcinka, nie prostej.** Sam znak iloczynu wektorowego wykrywa
przecięcie *nieskończonej prostej*, więc ktoś idący daleko poza linią zostałby
zliczony. Rzut punktu na odcinek (parametr `t` w `[0,1]`) eliminuje ten błąd —
regresja na to jest w `selftest` (krok 4/4 wymaga dokładnie jednego przejścia).

**Pre-roll przez bufor pierścieniowy.** Gdy system stwierdzi zdarzenie, jego
początek jest już przeszłością. Każda klatka ląduje najpierw w `deque`
o długości `pre_roll_s`, a decyzja o nagraniu wylewa bufor do pliku. Na klipie
widać, jak ktoś wchodzi — nie doklejkę od połowy.

**Wątek czytający zawsze najnowszą klatkę.** Przy RTSP wolniejszy konsument
powoduje zaleganie klatek w buforze i narastające opóźnienie. Dla źródeł live
wątek nadpisuje najnowszą klatkę i porzuca stare; dla plików czyta sekwencyjnie,
żeby nic nie zgubić.

**`ffmpeg` przez pipe zamiast `cv2.VideoWriter`.** Daje H.264 z `+faststart`,
odtwarzalny w przeglądarce bez transkodowania, i niezależność od kodeków
skompilowanych w OpenCV.

## Konfiguracja

Wszystko w [config.yaml](config.yaml). Współrzędne linii, stref i masek są
**znormalizowane do 0..1**, więc przeżywają zmianę rozdzielczości kamery.

Chmura — klucze przez zmienne środowiskowe, nie w pliku:

```bash
export HALLWATCH_S3_KEY=...
export HALLWATCH_S3_SECRET=...
```

## Prywatność i RODO

Najlepszym zabezpieczeniem jest **kadr**, nie software: kamera ustawiona tak, by
widziała wyłącznie własne drzwi, nie wymaga żadnego maskowania. To jest domyślne
założenie tego systemu — `privacy.masks` jest puste, a przy pustej liście
`PrivacyMasker.apply()` natychmiast zwraca klatkę bez zmian i zerowego narzutu.

Gdy jednak kadru nie da się tak ograniczyć (montaż wymuszony konstrukcją,
szerokokątny obiektyw łapiący cudze wejście), są narzędzia:

- `privacy.masks` — obszary zasłaniane **przed** detekcją, nagraniem i uploadem;
  detekcje z kotwicą w masce są odrzucane. `hallwatch zones` wyznacza je myszką
- `recording.retention_days` + `hallwatch prune` — ograniczona retencja
- brak rozpoznawania twarzy i identyfikacji osób: system liczy anonimowe
  obiekty klasy `person`, `track_id` żyje tylko w pamięci procesu
- lokalny zapis domyślnie, chmura opcjonalnie i prywatnym bucketem

Warto też mieć widoczną informację o monitoringu — jest tania, a rozwiązuje
większość nieporozumień, zanim powstaną.

## Struktura

```text
src/hallwatch/
  capture.py    odczyt RTSP/webcam/plik + reconnect
  privacy.py    maski prywatności (blur / black / pixelate)
  motion.py     bramka MOG2
  detect.py     YOLO11 + ByteTrack
  counter.py    przekroczenia linii, obecność w strefach
  recorder.py   bufor pierścieniowy + ffmpeg H.264
  store.py      SQLite: zdarzenia, przejścia, statystyki
  audio.py      poziom dźwięku z RTSP przez ffmpeg
  cloud.py      upload S3-compatible w tle
  notify.py     push ntfy
  draw.py       nakładka: boxy, trajektorie, HUD
  pipeline.py   maszyna stanów IDLE → AWAKE → EVENT
  web.py        FastAPI: dashboard, MJPEG, API
  tools.py      scan / probe / edytor stref / selftest
```

## Warstwa analityczna

Zdarzenia z kamer sa zrodlem dla osobnego pipeline'u danych w
[analytics/](analytics/README.md): przyrostowy extract do Parquet, BigQuery
przez tabele zewnetrzne nad GCS, modelowanie w dbt (9 modeli, 23 testy),
prognoza ruchu na 24 h i wykrywanie anomalii, orkiestrowane Airflowem.
Caly stos da sie uruchomic lokalnie na DuckDB, bez konta w chmurze.

## Dalsze kroki

- ReID między zdarzeniami (ta sama osoba wróciła po godzinie?)
- klasyfikacja dźwięku YAMNet: pukanie / trzaśnięcie drzwiami / krzyk
- Docker + `systemd` na Raspberry Pi 5 lub mini-PC, żeby MacBook nie chodził 24/7
- eksport metryk do Prometheusa, alerty na anomalie w nocy
