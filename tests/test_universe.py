import pandas as pd

from src.data.demo import generate_demo_data
from src.data.pairs import load_pairs
from src.data.status import build_data_status


def test_bundled_registry_is_full_universe():
    pairs = load_pairs(active_only=True)
    assert len(pairs) >= 100
    assert pairs["a_code"].is_unique
    assert pairs["h_code"].is_unique
    assert {"a_code", "h_code", "industry", "source"}.issubset(pairs.columns)


def test_demo_covers_current_registry():
    pairs = load_pairs(active_only=True)
    prices = generate_demo_data()
    assert prices["company_id"].nunique() == len(pairs)
    status = build_data_status(prices, len(pairs))
    assert status.mode == "demo"
    assert status.previous_snapshot_count == 202


def test_numeric_codes_are_zero_padded_correctly():
    from src.data.pairs import _clean_code
    assert _clean_code(38.0, 5) == "00038"
    assert _clean_code("600030.0", 6) == "600030"
