from __future__ import annotations

import math
from itertools import permutations
from typing import Callable

import numpy as np
import pandas as pd


def a_premium(a_close: float, h_close: float, fx: float) -> float:
    """A-share premium over H-share, in percentage points."""
    if min(a_close, h_close, fx) <= 0:
        return float("nan")
    return (a_close / (h_close * fx) - 1.0) * 100.0


def add_premium_features(frame: pd.DataFrame, rolling_window: int = 60) -> pd.DataFrame:
    result = frame.copy().sort_values("date")
    result["a_premium_pct"] = (
        result["a_close"] / (result["h_close"] * result["fx_cnh_per_hkd"]) - 1.0
    ) * 100.0
    result["premium_change_pp"] = result["a_premium_pct"].diff()
    rolling_mean = result["premium_change_pp"].rolling(rolling_window, min_periods=20).mean()
    rolling_std = result["premium_change_pp"].rolling(rolling_window, min_periods=20).std(ddof=0)
    result["change_z"] = (result["premium_change_pp"] - rolling_mean) / rolling_std.replace(0, np.nan)
    result["premium_rolling_median"] = result["a_premium_pct"].rolling(
        rolling_window, min_periods=20
    ).median()
    result["premium_distance"] = result["a_premium_pct"] - result["premium_rolling_median"]
    result["premium_percentile"] = result["a_premium_pct"].rolling(
        252, min_periods=60
    ).rank(pct=True)
    return result


def shapley_contributions(previous: pd.Series, current: pd.Series) -> dict[str, float]:
    """Exact three-factor Shapley decomposition of the premium change.

    The factors are A price, H price and HKD/CNH. Contributions sum exactly to
    the observed premium change, avoiding order-dependent sequential attribution.
    """
    keys = ["a_close", "h_close", "fx_cnh_per_hkd"]

    def value(state: dict[str, float]) -> float:
        return a_premium(state["a_close"], state["h_close"], state["fx_cnh_per_hkd"])

    base = {key: float(previous[key]) for key in keys}
    target = {key: float(current[key]) for key in keys}
    contributions = {key: 0.0 for key in keys}

    for order in permutations(keys):
        state = base.copy()
        before = value(state)
        for key in order:
            state[key] = target[key]
            after = value(state)
            contributions[key] += after - before
            before = after

    divisor = math.factorial(len(keys))
    return {
        "a_contribution_pp": contributions["a_close"] / divisor,
        "h_contribution_pp": contributions["h_close"] / divisor,
        "fx_contribution_pp": contributions["fx_cnh_per_hkd"] / divisor,
    }
