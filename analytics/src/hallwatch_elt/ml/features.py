"""Feature engineering for the hourly traffic forecast.

Design decision: we forecast 24 hours ahead with the DIRECT method, not the
recursive one. The model receives only features that are known 24 hours before the
forecast hour (lags of 24 h or more). This removes the need to feed the model its
own predictions, error does not accumulate, and the offline evaluation matches what
the model will actually see in production.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

HORIZON_H = 24
TARGET = "events"

# opoznienia dostepne na >= 24 h przed prognozowana godzina
LAGS = (24, 25, 48, 168, 169)
ROLLINGS = (24, 168)


def add_calendar(df: pd.DataFrame) -> pd.DataFrame:
    ts = pd.to_datetime(df["hour_ts"])
    out = df.copy()
    out["hour_of_day"] = ts.dt.hour
    out["day_of_week"] = ts.dt.dayofweek  # 0=poniedzialek
    out["is_weekend"] = (out["day_of_week"] >= 5).astype(int)
    out["day_of_month"] = ts.dt.day
    # cyclical encoding: 23:00 and 00:00 should be close to each other,
    # while numerically 23 and 0 are as far apart as possible
    out["hour_sin"] = np.sin(2 * np.pi * out["hour_of_day"] / 24)
    out["hour_cos"] = np.cos(2 * np.pi * out["hour_of_day"] / 24)
    out["dow_sin"] = np.sin(2 * np.pi * out["day_of_week"] / 7)
    out["dow_cos"] = np.cos(2 * np.pi * out["day_of_week"] / 7)
    return out


def add_lags(df: pd.DataFrame, target: str = TARGET) -> pd.DataFrame:
    out = df.sort_values("hour_ts").copy()
    for lag in LAGS:
        out[f"lag_{lag}"] = out[target].shift(lag)
    for win in ROLLINGS:
        # shift(HORIZON_H) first: the mean must not see the future
        out[f"roll_mean_{win}"] = out[target].shift(HORIZON_H).rolling(win, min_periods=2).mean()
        out[f"roll_std_{win}"] = out[target].shift(HORIZON_H).rolling(win, min_periods=2).std()
    return out


def feature_columns(df: pd.DataFrame) -> list[str]:
    base = [
        "hour_of_day", "day_of_week", "is_weekend", "day_of_month",
        "hour_sin", "hour_cos", "dow_sin", "dow_cos",
    ]
    lagged = [c for c in df.columns if c.startswith(("lag_", "roll_"))]
    return base + sorted(lagged)


def build(df: pd.DataFrame, target: str = TARGET) -> pd.DataFrame:
    """Turns the hourly mart into a feature matrix for a single camera."""
    out = add_calendar(df)
    out = add_lags(out, target)
    return out.dropna(subset=[c for c in out.columns if c.startswith("lag_")])
