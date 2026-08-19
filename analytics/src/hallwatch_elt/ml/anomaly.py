"""Anomaly detection in traffic.

Two independent signals, because they catch different things:

1. Forecast residuals with robust standardisation (median and MAD instead of mean
   and standard deviation). Anomalies are by definition extreme values, so with a
   plain standard deviation the anomalies themselves would inflate it and hide
   from the detector. This answers the question: "was traffic AT THIS HOUR
   different from what it should have been?"

2. IsolationForest over the feature space, which catches unusual COMBINATIONS
   (for example heavy traffic in the middle of the night on a weekday), even when
   the count alone is not a record.

The results are combined: an alarm fires when either triggers, recording which one.
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
MAD_TO_SIGMA = 1.4826  # MAD -> standard deviation factor for a normal distribution


def robust_z(values: np.ndarray) -> np.ndarray:
    median = np.nanmedian(values)
    mad = np.nanmedian(np.abs(values - median))
    scale = MAD_TO_SIGMA * mad
    if scale < 1e-9:  # data almost constant, no meaningful scale
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
            ["residual+isolation", "unusual volume", "unusual feature combination"],
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
        "Anomalies: %d of %d hours (%.2f%%)",
        int(out["is_anomaly"].sum()), len(out), 100 * out["is_anomaly"].mean(),
    )
    return out
