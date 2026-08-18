"""Generator realistycznej historii dla dema i testow ML.

Bez tego caly stos analityczny nie ma czego liczyc: swiezy pipeline CV ma pusta
baze, a na plaskim szumie model nie pokaze niczego ciekawego. Generujemy wiec
ruch z prawdziwymi wzorcami, ktore model MA szanse odkryc:

  - dobowy rytm (szczyt poranny i popoludniowy, martwa noc)
  - tygodniowy (weekend inny niz dni robocze)
  - swieta i anomalie (impreza u sasiada, korek na ulicy)
  - szum Poissona, bo zliczenia sa dyskretne i losowe

Dane sa syntetyczne i tak opisane - to material demonstracyjny, nie pomiar.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# profil dobowy: mnoznik natezenia na kazda godzine (0-23)
CORRIDOR_WEEKDAY = [
    0.02, 0.01, 0.01, 0.01, 0.03, 0.15, 0.55, 1.00, 0.85, 0.45, 0.35, 0.40,
    0.45, 0.40, 0.40, 0.55, 0.85, 1.00, 0.90, 0.70, 0.50, 0.35, 0.18, 0.06,
]
CORRIDOR_WEEKEND = [
    0.05, 0.04, 0.03, 0.02, 0.02, 0.04, 0.10, 0.20, 0.35, 0.55, 0.70, 0.80,
    0.85, 0.80, 0.75, 0.75, 0.80, 0.85, 0.80, 0.70, 0.60, 0.45, 0.30, 0.12,
]
STREET_WEEKDAY = [
    0.08, 0.05, 0.04, 0.04, 0.10, 0.30, 0.75, 1.00, 0.95, 0.70, 0.60, 0.62,
    0.68, 0.65, 0.70, 0.85, 1.00, 0.98, 0.80, 0.60, 0.45, 0.32, 0.20, 0.12,
]
STREET_WEEKEND = [
    0.15, 0.10, 0.07, 0.05, 0.05, 0.08, 0.18, 0.30, 0.45, 0.62, 0.75, 0.82,
    0.85, 0.85, 0.82, 0.80, 0.78, 0.72, 0.65, 0.55, 0.45, 0.38, 0.28, 0.20,
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, camera TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL, started_at REAL NOT NULL,
    ended_at REAL, max_persons INTEGER DEFAULT 0, count_in INTEGER DEFAULT 0,
    count_out INTEGER DEFAULT 0, peak_dbfs REAL, clip_path TEXT, snapshot_path TEXT,
    cloud_key TEXT, sampled INTEGER DEFAULT 0, sample_weight REAL DEFAULT 1.0, meta TEXT);
CREATE TABLE IF NOT EXISTS crossings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER, ts REAL NOT NULL,
    track_id INTEGER, direction TEXT);
CREATE TABLE IF NOT EXISTS minute_stats (
    camera TEXT NOT NULL DEFAULT '', minute INTEGER NOT NULL,
    frames INTEGER DEFAULT 0, motion INTEGER DEFAULT 0,
    persons INTEGER DEFAULT 0, count_in INTEGER DEFAULT 0, count_out INTEGER DEFAULT 0,
    PRIMARY KEY (camera, minute));
"""


def _profile(camera: str, is_weekend: bool) -> list[float]:
    if camera == "ulica":
        return STREET_WEEKEND if is_weekend else STREET_WEEKDAY
    return CORRIDOR_WEEKEND if is_weekend else CORRIDOR_WEEKDAY


def generate(
    db_path: Path,
    days: int = 90,
    end: datetime | None = None,
    seed: int = 42,
    tz: str = "Europe/Warsaw",
) -> dict[str, int]:
    """Tworzy baze SQLite w schemacie HallWatch z syntetyczna historia."""
    rng = np.random.default_rng(seed)
    # wzorce dobowe budujemy w czasie LOKALNYM - "szczyt o 7 rano" ma znaczyc
    # 7 rano u mieszkanca, a nie 7 UTC. Do bazy i tak leci epoch.
    local = ZoneInfo(tz)
    end = end or datetime.now(local).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    # (nazwa, kind, bazowe natezenie/h, srednia dlugosc s, duty_cycle)
    # Ulica pracuje w trybie probkowania: obserwujemy 5 minut na godzine, wiec
    # ZAPISUJEMY tylko ta czesc ruchu, ktora naprawde bylo widac, i doklejamy
    # wage 1/duty do ekstrapolacji. Dzieki temu warstwa analityczna ma szanse
    # odtworzyc prawdziwe natezenie, a demo nie klamie o tym, co widziano.
    cameras = [
        ("korytarz", "person", 3.2, 22.0, 1.0),
        ("ulica", "vehicle", 38.0, 9.0, 5.0 / 60.0),
    ]

    events: list[tuple] = []
    crossings: list[tuple] = []
    minute_rows: dict[int, list[int]] = {}
    event_id = 0
    track_id = 0

    total_hours = days * 24
    for h in range(total_hours):
        hour_start = start + timedelta(hours=h)
        hod = hour_start.hour
        is_weekend = hour_start.weekday() >= 5

        for camera, kind, base_rate, mean_dur, duty in cameras:
            rate = base_rate * _profile(camera, is_weekend)[hod]

            # anomalie: rzadkie, ale wyrazne - material dla detektora
            anomaly = 1.0
            if rng.random() < 0.004:  # ~ raz na 10 dni na kamere
                anomaly = rng.uniform(3.0, 6.0)
            # sezonowa fala tygodniowa, zeby model mial czego sie uczyc poza doba
            rate *= 1.0 + 0.12 * math.sin(2 * math.pi * h / (24 * 7))

            # obserwujemy tylko duty * godzine, wiec tyle zdarzen widzimy
            n_events = rng.poisson(max(0.001, rate * anomaly * duty))
            sampled = duty < 1.0
            weight = 1.0 / duty
            for _ in range(int(n_events)):
                event_id += 1
                offset = rng.uniform(0, 3600)
                started = (hour_start.timestamp()) + offset
                duration = max(2.0, rng.gamma(2.0, mean_dur / 2.0))
                ended = started + duration

                if kind == "person":
                    n_obj = int(rng.choice([1, 1, 1, 2, 2, 3], p=[.45, .2, .1, .13, .07, .05]))
                else:
                    n_obj = int(max(1, rng.poisson(1.4)))

                # kierunki: w dzien wiecej wyjsc rano, wiecej wejsc wieczorem
                lean_out = 0.65 if 5 <= hod <= 10 else (0.35 if 15 <= hod <= 21 else 0.5)
                c_out = int(rng.binomial(n_obj, lean_out))
                c_in = n_obj - c_out

                has_media = kind == "person"  # ulicy nie archiwizujemy
                events.append((
                    event_id, camera, kind, started, ended, n_obj, c_in, c_out,
                    float(rng.uniform(-52, -28)) if rng.random() < 0.25 else None,
                    f"data/clips/{camera}/{int(started)}.mp4" if has_media else None,
                    f"data/clips/{camera}/{int(started)}.jpg" if has_media else None,
                    None, int(sampled), weight,
                    json.dumps({"mode": "sampling" if sampled else "on_demand"}, ensure_ascii=False),
                ))

                for direction, count in (("wejscie", c_in), ("wyjscie", c_out)):
                    for _ in range(count):
                        track_id += 1
                        crossings.append((
                            None, event_id, started + rng.uniform(0, duration), track_id, direction
                        ))

                minute = int(started // 60)
                row = minute_rows.setdefault((camera, minute), [0, 0, 0, 0, 0])
                row[0] += int(duration * 12)      # frames
                row[1] += 1                        # motion
                row[2] = max(row[2], n_obj)        # persons (szczyt)
                row[3] += c_in
                row[4] += c_out

    conn.executemany(
        "INSERT INTO events(id,camera,kind,started_at,ended_at,max_persons,count_in,count_out,"
        "peak_dbfs,clip_path,snapshot_path,cloud_key,sampled,sample_weight,meta) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        events,
    )
    conn.executemany(
        "INSERT INTO crossings(id,event_id,ts,track_id,direction) VALUES(?,?,?,?,?)", crossings
    )
    conn.executemany(
        "INSERT INTO minute_stats(camera,minute,frames,motion,persons,count_in,count_out) "
        "VALUES(?,?,?,?,?,?,?)",
        [(cam, m, *v) for (cam, m), v in sorted(minute_rows.items())],
    )
    conn.commit()
    conn.close()

    stats = {"events": len(events), "crossings": len(crossings), "minutes": len(minute_rows)}
    log.info("Wygenerowano %s od %s do %s", stats, start.date(), end.date())
    return stats
