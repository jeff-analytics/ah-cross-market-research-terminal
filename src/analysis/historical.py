from __future__ import annotations

import numpy as np
import pandas as pd


def _future_value(series: pd.Series, horizon: int) -> pd.Series:
    return series.shift(-horizon)


def add_forward_outcomes(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy().sort_values("date")
    for horizon in (1, 5, 20):
        result[f"future_premium_{horizon}d"] = _future_value(result["a_premium_pct"], horizon)
        result[f"premium_change_{horizon}d"] = (
            result[f"future_premium_{horizon}d"] - result["a_premium_pct"]
        )
        current_distance = (result["a_premium_pct"] - result["premium_rolling_median"]).abs()
        future_distance = (
            result[f"future_premium_{horizon}d"] - result["premium_rolling_median"]
        ).abs()
        result[f"converged_{horizon}d"] = future_distance < current_distance
    return result


def find_similar_events(frame: pd.DataFrame, top_n: int = 8) -> pd.DataFrame:
    data = add_forward_outcomes(frame).dropna(
        subset=["a_premium_pct", "premium_change_pp", "change_z", "premium_percentile"]
    )
    if len(data) < 30:
        return pd.DataFrame()
    current = data.iloc[-1]
    history = data.iloc[:-20].copy()
    if history.empty:
        return pd.DataFrame()

    features = ["premium_percentile", "premium_change_pp", "change_z"]
    for col in features:
        std = history[col].std(ddof=0)
        if not np.isfinite(std) or std == 0:
            history[f"z_{col}"] = 0.0
            current_z = 0.0
        else:
            mean = history[col].mean()
            history[f"z_{col}"] = (history[col] - mean) / std
            current_z = (current[col] - mean) / std
        history[f"distance_{col}"] = (history[f"z_{col}"] - current_z) ** 2

    distance_cols = [f"distance_{col}" for col in features]
    history["similarity_distance"] = np.sqrt(history[distance_cols].sum(axis=1))
    cols = [
        "date",
        "a_premium_pct",
        "premium_change_pp",
        "change_z",
        "similarity_distance",
        "premium_change_1d",
        "premium_change_5d",
        "premium_change_20d",
        "converged_1d",
        "converged_5d",
        "converged_20d",
    ]
    return history.nsmallest(top_n, "similarity_distance")[cols].reset_index(drop=True)


def summarize_analogs(analogs: pd.DataFrame) -> dict[str, float | int | None]:
    if analogs.empty:
        return {"count": 0, "convergence_1d": None, "convergence_5d": None, "convergence_20d": None}
    return {
        "count": int(len(analogs)),
        "convergence_1d": float(analogs["converged_1d"].mean()),
        "convergence_5d": float(analogs["converged_5d"].mean()),
        "convergence_20d": float(analogs["converged_20d"].mean()),
    }
