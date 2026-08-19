# HallWatch

**Zamień dowolną kamerę w prywatny, inteligentny monitoring.** Liczenie osób
i pojazdów, zdarzenia ruchu i dźwięku, nagrania zaczynające się *zanim* coś
się stało, maski prywatności, dashboard na żywo - wszystko self-hosted, bez
kont, bez chmury, bez abonamentów.

Działa z: kamerą laptopa · dowolną kamerą RTSP (~100 zł) · starym telefonem
jako kamerą (**0 zł** - patrz [docs/cameras.md](docs/cameras.md)) · plikami wideo.

*(English version: [README.md](README.md) - the English README is the
authoritative one; this translation may lag behind.)*

## Szybki start

**Wymagania:** Python 3.10-3.13 · [ffmpeg](https://ffmpeg.org) w PATH
(macOS: `brew install ffmpeg` · Debian/Ubuntu: `sudo apt install ffmpeg` ·
Windows: `winget install ffmpeg`) · opcjonalnie [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Dzierzyn/hallwatch && cd hallwatch
make install        # albo: make install-pip  (bez uv)
make test           # 30 s, kamera niepotrzebna
make run            # → http://127.0.0.1:8000
```

To wszystko - domyślna konfiguracja patrzy przez **kamerę laptopa** i liczy
osoby przekraczające linię. Przejdź przez kadr i patrz, jak licznik rośnie.

Pierwsze uruchomienie samo pobiera model YOLO11-nano (~6 MB).

### Docker (najlepszy dla kamer RTSP)

```bash
# w config.yaml ustaw source: "rtsp://user:haslo@IP-KAMERY:554/stream1"
docker compose up   # → http://localhost:8000
```

### Masz prawdziwą kamerę?

```bash
make scan                                   # znajdź kamery RTSP w sieci
make probe SOURCE='rtsp://user:haslo@ip:554/stream1'   # sprawdź strumień
make zones                                  # narysuj linie zliczające klikaniem
```

Wpisz URL do `config.yaml` pod `source:` i ponownie `make run`.
**Nie masz kamery?** Stary telefon działa świetnie i nic nie kosztuje -
[docs/cameras.md](docs/cameras.md) opisuje to oraz które tanie kamery kupić
(i których unikać - kamery bateryjne przeczytaj *przed* zakupem).

## Co robi

Detekcja ruchu (bramka MOG2 ~1 ms przed siecią) · detekcja i tracking osób
lub pojazdów (YOLO11 + ByteTrack) · liczenie przekroczeń linii z kierunkiem ·
strefy obecności · zdarzenia dźwiękowe · nagrywanie z pre-rollem · maski
prywatności nakładane **przed** detekcją i zapisem · SQLite · dashboard MJPEG
z osią zdarzeń · powiadomienia ntfy · opcjonalny backup S3 · opcjonalna
[warstwa analityczna](analytics/) (dbt + prognoza ruchu + anomalie).

Trzy tryby pracy kamery: `continuous` (zasilanie stałe), `on_demand`
(kamery bateryjne - łączy się na sygnał i pozwala kamerze zasnąć),
`sampling` (statystyki ruchu z uczciwą ekstrapolacją z próbki).

## Prywatność w konstrukcji

- Maski są **pierwszym** krokiem pipeline'u: zamaskowany obszar nigdy nie
  trafia do detekcji, na dysk, do dashboardu ani do chmury.
- Zero rozpoznawania twarzy i tożsamości; identyfikatory śledzenia żyją tylko
  w pamięci procesu.
- Dane lokalnie domyślnie; chmura to opt-in do Twojego własnego bucketa.
- Ograniczona retencja: `make prune`.

Jeśli kamera widzi drzwi lub okno sąsiada: najpierw przestaw kadr, resztę
zamaskuj, powieś informację o monitoringu i sprawdź lokalne prawo - w UE
nagrywanie przestrzeni wspólnej zwykle uruchamia obowiązki RODO.

## Dashboard z innych urządzeń

Dashboard domyślnie **nie ma uwierzytelnienia** i słucha na `127.0.0.1`.
Żeby wejść z telefonu, ustaw w `config.yaml` **obie** rzeczy: `host: "0.0.0.0"`
oraz `auth_token: "cos-dlugiego-i-losowego"`, potem otwórz
`http://<ip>:8000/?token=<twoj-token>`. Do dostępu spoza domu użyj
Tailscale/WireGuard, nie przekierowania portów.

## Współtworzenie

Najcenniejszy wkład to **raport zgodności kamery** - jest do tego szablon
issue, kod niepotrzebny. Setup deweloperski: [CONTRIBUTING.md](CONTRIBUTING.md).
Zgłoszenia bezpieczeństwa: [SECURITY.md](SECURITY.md).

## Licencja

[MIT](LICENSE).
