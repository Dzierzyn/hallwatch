"""Hourly traffic forecast, 24 hours ahead.

Model: HistGradientBoostingRegressor over calendar features and lags.
Reference point: a naive seasonal forecast, "the same as this hour last week".
This matters, because without a baseline any MAE number sounds clever, and only
the ratio against the naive forecast says whether the model contributes anything.

Metric: MAE, not MAPE. MAPE divides by the actual value, and at night traffic is
zero, so the percentages blow up to infinity and the metric lies.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

from . import features as F

log = logging.getLogger(__name__)

TEST_DAYS = 14


@dataclass
class Metrics:
    camera: str
    n_train: int
    n_test: int
    mae_model: float
    mae_baseline: float
    rmse_model: float
    improvement_pct: float

    def line(self) -> str:
        verdict = "better" if self.improvement_pct > 0 else "WORSE"
        return (
            f"  {self.camera:10s} MAE {self.mae_model:6.3f} vs naive {self.mae_baseline:6.3f}"
            f"  -> {self.improvement_pct:+5.1f}% ({verdict}), train={self.n_train} test={self.n_test}"
        )


def _fit_one(hist: pd.DataFrame, camera: str) -> tuple[Metrics, pd.DataFrame]:
    feat = F.build(hist)
    cols = F.feature_columns(feat)

    cutoff = feat["hour_ts"].max() - pd.Timedelta(days=TEST_DAYS)
    train = feat[feat["hour_ts"] <= cutoff]
    test = feat[feat["hour_ts"] > cutoff]
    if len(train) < 200 or test.empty:
        raise ValueError(f"not enough data for camera {camera}: train={len(train)} test={len(test)}")

    model = HistGradientBoostingRegressor(
        max_iter=400, learning_rate=0.06, max_depth=6,
        min_samples_leaf=20, l2_regularization=1.0, random_state=42,
    )
    model.fit(train[cols], train[F.TARGET])

    pred = np.clip(model.predict(test[cols]), 0, None)
    baseline = test["lag_168"].to_numpy()  # the same hour a week ago

    mae_m = mean_absolute_error(test[F.TARGET], pred)
    mae_b = mean_absolute_error(test[F.TARGET], baseline)
    metrics = Metrics(
        camera=camera,
        n_train=len(train),
        n_test=len(test),
        mae_model=float(mae_m),
        mae_baseline=float(mae_b),
        rmse_model=float(np.sqrt(mean_squared_error(test[F.TARGET], pred))),
        improvement_pct=float((mae_b - mae_m) / mae_b * 100) if mae_b else 0.0,
    )

    backtest = pd.DataFrame({
        "hour_ts": test["hour_ts"].to_numpy(),
        "camera": camera,
        "actual": test[F.TARGET].to_numpy(dtype=float),
        "predicted": pred,
        "baseline": baseline.astype(float),
        "is_forecast": False,
    })

    # the forecast proper: 24 h ahead. All lags are >= 24 h, so
    # every value needed is already known, with no recursion.
    last = hist["hour_ts"].max()
    future = pd.DataFrame({
        "hour_ts": pd.date_range(last + pd.Timedelta(hours=1), periods=F.HORIZON_H, freq="h"),
        F.TARGET: np.nan,
    })
    extended = pd.concat([hist[["hour_ts", F.TARGET]], future], ignore_index=True)
    ext_feat = F.add_lags(F.add_calendar(extended))
    horizon = ext_feat[ext_feat["hour_ts"] > last].copy()
    horizon_pred = np.clip(model.predict(horizon[cols].astype(float)), 0, None)

    forecast = pd.DataFrame({
        "hour_ts": horizon["hour_ts"].to_numpy(),
        "camera": camera,
        "actual": np.nan,
        "predicted": horizon_pred,
        "baseline": horizon["lag_168"].to_numpy(dtype=float),
        "is_forecast": True,
    })

    return metrics, pd.concat([backtest, forecast], ignore_index=True)


def run(hourly: pd.DataFrame) -> tuple[pd.DataFrame, list[Metrics]]:
    """Trains a separate model per camera and returns (predictions, metrics)."""
    frames, all_metrics = [], []
    for camera, group in hourly.groupby("camera"):
        hist = (
            group[["hour_ts", F.TARGET]]
            .assign(hour_ts=lambda d: pd.to_datetime(d["hour_ts"]))
            .sort_values("hour_ts")
            .reset_index(drop=True)
        )
        try:
            metrics, preds = _fit_one(hist, str(camera))
        except ValueError as exc:
            log.warning("Skipping camera %s: %s", camera, exc)
            continue
        all_metrics.append(metrics)
        frames.append(preds)
        log.info(metrics.line())

    if not frames:
        return pd.DataFrame(), []
    out = pd.concat(frames, ignore_index=True)
    out["residual"] = out["actual"] - out["predicted"]
    return out, all_metrics


def metrics_frame(metrics: list[Metrics]) -> pd.DataFrame:
    return pd.DataFrame([asdict(m) for m in metrics])
