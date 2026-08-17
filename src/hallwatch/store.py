"""Trwaly zapis zdarzen w SQLite.

SQLite, nie pliki JSON: dostajemy zapytania po czasie, agregaty do wykresow i
atomowosc, a caly "serwer bazy" to jeden plik obok klipow.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,
    started_at    REAL NOT NULL,
    ended_at      REAL,
    max_persons   INTEGER DEFAULT 0,
    count_in      INTEGER DEFAULT 0,
    count_out     INTEGER DEFAULT 0,
    peak_dbfs     REAL,
    clip_path     TEXT,
    snapshot_path TEXT,
    cloud_key     TEXT,
    meta          TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_started ON events(started_at DESC);

CREATE TABLE IF NOT EXISTS crossings (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id  INTEGER,
    ts        REAL NOT NULL,
    track_id  INTEGER,
    direction TEXT,
    FOREIGN KEY(event_id) REFERENCES events(id)
);
CREATE INDEX IF NOT EXISTS idx_crossings_ts ON crossings(ts DESC);

CREATE TABLE IF NOT EXISTS minute_stats (
    minute     INTEGER PRIMARY KEY,
    frames     INTEGER DEFAULT 0,
    motion     INTEGER DEFAULT 0,
    persons    INTEGER DEFAULT 0,
    count_in   INTEGER DEFAULT 0,
    count_out  INTEGER DEFAULT 0
);
"""


@dataclass
class Event:
    id: int
    kind: str
    started_at: float
    ended_at: float | None
    max_persons: int
    count_in: int
    count_out: int
    peak_dbfs: float | None
    clip_path: str | None
    snapshot_path: str | None
    cloud_key: str | None
    meta: dict[str, Any]


class Store:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # -- zapis --------------------------------------------------------------
    def open_event(self, kind: str, started_at: float, meta: dict | None = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events(kind, started_at, meta) VALUES(?,?,?)",
                (kind, started_at, json.dumps(meta or {}, ensure_ascii=False)),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def close_event(self, event_id: int, **fields: Any) -> None:
        if not fields:
            return
        if "meta" in fields and isinstance(fields["meta"], dict):
            fields["meta"] = json.dumps(fields["meta"], ensure_ascii=False)
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._conn.execute(
                f"UPDATE events SET {cols} WHERE id=?", (*fields.values(), event_id)
            )
            self._conn.commit()

    def add_crossing(self, event_id: int | None, ts: float, track_id: int, direction: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO crossings(event_id, ts, track_id, direction) VALUES(?,?,?,?)",
                (event_id, ts, track_id, direction),
            )
            self._conn.commit()

    def bump_minute(
        self, ts: float, frames: int = 0, motion: int = 0, persons: int = 0,
        count_in: int = 0, count_out: int = 0,
    ) -> None:
        minute = int(ts // 60)
        with self._lock:
            self._conn.execute(
                """INSERT INTO minute_stats(minute, frames, motion, persons, count_in, count_out)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(minute) DO UPDATE SET
                     frames=frames+excluded.frames,
                     motion=motion+excluded.motion,
                     persons=MAX(persons, excluded.persons),
                     count_in=count_in+excluded.count_in,
                     count_out=count_out+excluded.count_out""",
                (minute, frames, motion, persons, count_in, count_out),
            )
            self._conn.commit()

    # -- odczyt -------------------------------------------------------------
    def recent_events(self, limit: int = 50, kind: str | None = None) -> list[Event]:
        q = "SELECT * FROM events"
        args: list[Any] = []
        if kind:
            q += " WHERE kind=?"
            args.append(kind)
        q += " ORDER BY started_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [self._row_to_event(r) for r in rows]

    def event(self, event_id: int) -> Event | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return self._row_to_event(row) if row else None

    def stats_since(self, since_ts: float) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM minute_stats WHERE minute>=? ORDER BY minute",
                (int(since_ts // 60),),
            ).fetchall()
        return [dict(r) for r in rows]

    def totals(self, since_ts: float | None = None) -> dict:
        since = since_ts if since_ts is not None else 0.0
        with self._lock:
            row = self._conn.execute(
                """SELECT COUNT(*) AS events,
                          COALESCE(SUM(count_in),0)  AS count_in,
                          COALESCE(SUM(count_out),0) AS count_out,
                          COALESCE(MAX(max_persons),0) AS peak_persons
                   FROM events WHERE started_at>=?""",
                (since,),
            ).fetchone()
        return dict(row) if row else {}

    def events_older_than(self, cutoff_ts: float) -> list[Event]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE started_at < ?", (cutoff_ts,)
            ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def clear_media(self, event_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE events SET clip_path=NULL, snapshot_path=NULL WHERE id=?", (event_id,)
            )
            self._conn.commit()

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> Event:
        d = dict(row)
        try:
            d["meta"] = json.loads(d.get("meta") or "{}")
        except json.JSONDecodeError:
            d["meta"] = {}
        return Event(**d)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def now() -> float:
    return time.time()
