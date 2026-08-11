import pandas as pd
import pytest

from src.analysis.premium import a_premium, shapley_contributions


def test_premium_formula():
    assert a_premium(12.0, 10.0, 1.0) == pytest.approx(20.0)


def test_shapley_sums_to_observed_change():
    previous = pd.Series({"a_close": 10.0, "h_close": 8.0, "fx_cnh_per_hkd": 0.9})
    current = pd.Series({"a_close": 10.5, "h_close": 7.8, "fx_cnh_per_hkd": 0.91})
    contrib = shapley_contributions(previous, current)
    observed = a_premium(**{"a_close": 10.5, "h_close": 7.8, "fx": 0.91}) - a_premium(
        **{"a_close": 10.0, "h_close": 8.0, "fx": 0.9}
    )
    assert sum(contrib.values()) == pytest.approx(observed)
