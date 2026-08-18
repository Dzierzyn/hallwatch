"""Wykrywanie anomalii w ruchu.

Dwa niezalezne sygnaly, bo lapia rozne rzeczy:

1. Reszty prognozy z odporna standaryzacja (mediana i MAD zamiast sredniej
   i odchylenia). Anomalie z definicji sa wartosciami skrajnymi, wiec gdyby
   uzyc zwyklego odchylenia standardowego, same anomalie by je zawyzyly
   i schowaly sie przed detektorem. To odpowiada na pytanie: "czy o TEJ porze
   ruch byl inny, niz powinien?"

2. IsolationForest na przestrzeni cech - lapie nietypowe KOMBINACJE
   (np. duzy ruch w srodku nocy w dzien roboczy), nawet gdy sama liczba
   nie jest rekordowa.

Wynik laczony: alarm, gdy zadziala ktorykolwiek, z zapisem ktory to byl.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from . import features as F

log = logging.getLogger(__name__)

Z_THRESHOLD = 3.5
CONTAMINATION = 0.01
MAD_TO_SIGMA = 1.4826  # przelicznik MAD -> odchylenie dla rozkladu normalnego


def robust_z(values: np.ndarray) -> np.ndarray:
    median = np.nanmedian(values)
    mad = np.nanmedian(np.abs(values - median))
    scale = MAD_TO_SIGMA * mad
    if scale < 1e-9:  # dane niemal stale - brak sensownej skali
        return np.zeros_like(values, dtype=float)
    return (values - median) / scale


def run(predictions: pd.DataFrame, hourly: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for camera, preds in predictions.groupby("camera"):
        scored = preds[~preds["is_forecast"]].copy()
        if scored.empty:
            continue
        scored["residual_z"] = robust_z(scored["residual"].to_numpy(dtype=float))

        hist = hourly[hourly["camera"] == camera].copy()
        hist["hour_ts"] = pd.to_datetime(hist["hour_ts"])
        feat = F.build(hist.sort_values("hour_ts").reset_index(drop=True))
        cols = F.feature_columns(feat)
        forest = IsolationForest(
            n_estimators=300, contamination=CONTAMINATION, random_state=42
        ).fit(feat[cols])
        iso = pd.DataFrame({
            "hour_ts": feat["hour_ts"].to_numpy(),
            "iso_score": forest.decision_function(feat[cols]),
            "iso_flag": forest.predict(feat[cols]) == -1,
        })

        merged = scored.merge(iso, on="hour_ts", how="left")
        merged["residual_flag"] = merged["residual_z"].abs() > Z_THRESHOLD
        merged["iso_flag"] = merged["iso_flag"].fillna(False)
        merged["is_anomaly"] = merged["residual_flag"] | merged["iso_flag"]
        merged["anomaly_reason"] = np.select(
            [
                merged["residual_flag"] & merged["iso_flag"],
                merged["residual_flag"],
                merged["iso_flag"],
            ],
            ["reszta+izolacja", "nietypowe natezenie", "nietypowa kombinacja cech"],
            default="",
        )
        frames.append(
            merged[[
                "hour_ts", "camera", "actual", "predicted", "residual",
                "residual_z", "iso_score", "residual_flag", "iso_flag",
                "is_anomaly", "anomaly_reason",
            ]]
        )

    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    log.info(
        "Anomalie: %d z %d godzin (%.2f%%)",
        int(out["is_anomaly"].sum()), len(out), 100 * out["is_anomaly"].mean(),
    )
    return out
