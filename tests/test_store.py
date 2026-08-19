"""SQLite store: schema, per-camera separation, migration of old databases."""

from __future__ import annotations

import sqlite3

from hallwatch.store import Store


def test_events_carry_camera_and_sampling(tmp_path):
    store = Store(tmp_path / "t.db")
    eid = store.open_event("vehicle", 1000.0, camera="street", sampled=True, sample_weight=12.0)
    store.close_event(eid, ended_at=1010.0, max_persons=2)
    ev = store.recent_events(limit=5)[0]
    assert (ev.camera, ev.kind, bool(ev.sampled), ev.sample_weight) == (
        "street",
        "vehicle",
        True,
        12.0,
    )
    store.close()


def test_events_filter_by_camera(tmp_path):
    store = Store(tmp_path / "t.db")
    store.open_event("person", 1.0, camera="hall")
    store.open_event("vehicle", 2.0, camera="street")
    assert {e.camera for e in store.recent_events()} == {"hall", "street"}
    assert [e.camera for e in store.recent_events(camera="hall")] == ["hall"]
    store.close()


def test_minute_stats_grain_is_camera_minute(tmp_path):
    store = Store(tmp_path / "t.db")
    ts = 120_000.0
    store.bump_minute(ts, camera="a", frames=10, count_in=1)
    store.bump_minute(ts, camera="b", frames=20)
    store.bump_minute(ts, camera="a", frames=5, count_in=2)  # same minute accumulates
    rows = {r["camera"]: r for r in store.stats_since(0)}
    assert rows["a"]["frames"] == 15 and rows["a"]["count_in"] == 3
    assert rows["b"]["frames"] == 20
    store.close()


def test_migration_adds_columns_to_old_database(tmp_path):
    """A database created before the multi-camera era must open cleanly."""
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
            started_at REAL NOT NULL, ended_at REAL, max_persons INTEGER DEFAULT 0,
            count_in INTEGER DEFAULT 0, count_out INTEGER DEFAULT 0, peak_dbfs REAL,
            clip_path TEXT, snapshot_path TEXT, cloud_key TEXT, meta TEXT);
        INSERT INTO events(kind, started_at, meta) VALUES('person', 5.0, '{}');
        """
    )
    conn.commit()
    conn.close()

    store = Store(db)  # must migrate, not crash
    ev = store.recent_events()[0]
    assert ev.camera == "" and ev.sample_weight == 1.0
    store.close()
