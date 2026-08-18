"""Extract: SQLite -> Parquet, przyrostowo, ze znacznikiem wodnym.

Dlaczego znacznik wodny, a nie pelny przeladunek: baza rosnie w nieskonczonosc,
a zdarzenia sa niezmienne po zamknieciu. Za kazdym razem zabieramy tylko to, co
przybylo od ostatniego przebiegu, i dopisujemy jako nowa partycje. Przebieg jest
idempotentny w obrebie partycji - powtorzenie nadpisuje ten sam plik, zamiast
duplikowac wiersze.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .config import Settings, settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TableSpec:
    name: str
    watermark_col: str  # kolumna rosnaca, po ktorej bierzemy przyrost
    time_col: str  # kolumna do partycjonowania po dacie (epoch w sekundach)
    unit: str = "s"  # 's' = epoch sekundy, 'min' = epoch minuty


TABLES: tuple[TableSpec, ...] = (
    TableSpec("events", "started_at", "started_at"),
    TableSpec("crossings", "ts", "ts"),
    TableSpec("minute_stats", "minute", "minute", unit="min"),
)


class Watermarks:
    """Prosty magazyn znacznikow: jeden JSON, jedna prawda."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, float] = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                log.warning("Uszkodzony plik znacznikow %s - zaczynam od zera", path)

    def get(self, table: str) -> float:
        return float(self._data.get(table, 0.0))

    def set(self, table: str, value: float) -> None:
        self._data[table] = float(value)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")


def _epoch_to_date(series: pd.Series, unit: str) -> pd.Series:
    seconds = series * 60 if unit == "min" else series
    return pd.to_datetime(seconds, unit="s", utc=True).dt.strftime("%Y-%m-%d")


def extract_table(
    conn: sqlite3.Connection, spec: TableSpec, wm: Watermarks, cfg: Settings
) -> tuple[int, list[Path]]:
    since = wm.get(spec.name)
    query = f"SELECT * FROM {spec.name} WHERE {spec.watermark_col} > ? ORDER BY {spec.watermark_col}"
    df = pd.read_sql_query(query, conn, params=(since,))
    if df.empty:
        log.info("%-13s brak nowych wierszy (znacznik %.0f)", spec.name, since)
        return 0, []

    df["_extracted_at"] = datetime.now(timezone.utc).isoformat()
    df["_partition_date"] = _epoch_to_date(df[spec.time_col], spec.unit)

    written: list[Path] = []
    for day, chunk in df.groupby("_partition_date", sort=True):
        out_dir = cfg.raw_dir / spec.name / f"dt={day}"
        out_dir.mkdir(parents=True, exist_ok=True)
        # nazwa pliku deterministyczna wzgledem zakresu -> powtorzenie nadpisuje
        lo = chunk[spec.watermark_col].min()
        hi = chunk[spec.watermark_col].max()
        path = out_dir / f"part-{int(lo)}-{int(hi)}.parquet"
        chunk.drop(columns=["_partition_date"]).to_parquet(path, index=False)
        written.append(path)

    wm.set(spec.name, float(df[spec.watermark_col].max()))
    log.info("%-13s +%d wierszy w %d partycjach", spec.name, len(df), len(written))
    return len(df), written


def run(cfg: Settings | None = None, full_refresh: bool = False) -> dict[str, int]:
    cfg = cfg or settings
    cfg.ensure_dirs()
    if not cfg.source_db.exists():
        raise FileNotFoundError(
            f"Nie ma bazy zrodlowej: {cfg.source_db}\n"
            "Uruchom pipeline CV albo wygeneruj dane demo: make seed"
        )

    wm_path = cfg.state_dir / "watermarks.json"
    if full_refresh and wm_path.exists():
        wm_path.unlink()
        log.info("full_refresh: znaczniki wyzerowane")
    wm = Watermarks(wm_path)

    conn = sqlite3.connect(f"file:{cfg.source_db}?mode=ro", uri=True)
    try:
        counts = {spec.name: extract_table(conn, spec, wm, cfg)[0] for spec in TABLES}
    finally:
        conn.close()
    wm.save()
    return counts
