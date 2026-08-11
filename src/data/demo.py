from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd

from src.config import DEMO_SNAPSHOT_FILE, PRICES_FILE, SETTINGS
from src.data.pairs import bootstrap_registry, load_pairs


def _stable_seed(text: str, base_seed: int) -> int:
    digest = hashlib.sha256(f"{base_seed}:{text}".encode()).hexdigest()
    return int(digest[:8], 16)


def _demo_snapshot_config() -> dict:
    if not DEMO_SNAPSHOT_FILE.exists():
        raise FileNotFoundError(
            f"Missing offline demo metadata: {DEMO_SNAPSHOT_FILE}. "
            "The demo snapshot date must be explicit so synthetic data cannot silently look current."
        )
    payload = json.loads(DEMO_SNAPSHOT_FILE.read_text(encoding='utf-8'))
    if not payload.get('snapshot_end'):
        raise ValueError('demo_snapshot.json must define snapshot_end')
    return payload


def generate_demo_data(seed: int | None = None, force_registry: bool = False, end: str | None = None) -> pd.DataFrame:
    """Create deterministic full-universe demonstration data.

    This is an explicitly dated offline preview snapshot. It is never treated as live
    or as a completed online history baseline. The snapshot date lives in
    data/demo_snapshot.json rather than in production code.
    """
    cfg = _demo_snapshot_config()
    seed = int(seed if seed is not None else cfg.get('seed', 0))
    trading_days = int(cfg.get('trading_days') or SETTINGS.bootstrap_demo_days)
    snapshot_end = pd.Timestamp(end or cfg['snapshot_end'])

    bootstrap_registry(force=force_registry)
    pairs = load_pairs(active_only=True)
    dates = pd.bdate_range(end=snapshot_end, periods=trading_days)
    n = len(dates)
    market_rng = np.random.default_rng(seed)
    market_a = market_rng.normal(0.0001, 0.0095, n)
    market_h = market_rng.normal(0.0, 0.0115, n)
    fx = 0.92 * np.exp(np.cumsum(market_rng.normal(0, 0.0010, n)))

    industry_factors = {
        industry: market_rng.normal(0.00003, 0.0055, n)
        for industry in pairs["industry"].unique()
    }
    records: list[pd.DataFrame] = []
    anomaly_codes = {"002594": "h_drop", "601318": "a_rise", "600030": "split", "601628": "halt", "600028": "dividend"}

    for row in pairs.itertuples(index=False):
        rng = np.random.default_rng(_stable_seed(row.company_id, seed))
        base_a = 4.0 + (int(row.a_code[-3:]) % 260) / 3.5
        base_premium = 0.08 + (int(row.h_code[-3:]) % 55) / 100
        common = industry_factors[row.industry]
        company = rng.normal(0, 0.0075, n)
        a_ret = 0.65 * market_a + common + 0.45 * company + rng.normal(0, 0.0038, n)
        h_ret = 0.65 * market_h + common + 0.38 * company + rng.normal(0, 0.0050, n)

        event = anomaly_codes.get(row.a_code)
        if event == "h_drop":
            h_ret[-1] -= 0.070
        elif event == "a_rise":
            a_ret[-1] += 0.045
        elif event == "split":
            a_ret[-1] += 0.020
            h_ret[-1] -= 0.045
        elif event == "halt":
            h_ret[-3:] = 0.0
            a_ret[-1] += 0.030
        elif event == "dividend":
            h_ret[-1] -= 0.035

        a_close = base_a * np.exp(np.cumsum(a_ret))
        h0 = base_a / ((1 + base_premium) * fx[0])
        h_close = h0 * np.exp(np.cumsum(h_ret))
        a_volume = (rng.lognormal(16.2, 0.45, n)).astype(int)
        h_volume = (rng.lognormal(15.6, 0.55, n)).astype(int)
        if event == "halt":
            h_volume[-3:] = 0
        ex_dividend_h = np.zeros(n, dtype=int)
        single_side_halt = np.zeros(n, dtype=int)
        if event == "dividend":
            ex_dividend_h[-1] = 1
        if event == "halt":
            single_side_halt[-1] = 1

        records.append(pd.DataFrame({
            "date": dates,
            "company_id": row.company_id,
            "company_name": row.company_name,
            "a_code": row.a_code,
            "h_code": row.h_code,
            "a_ticker": row.a_ticker,
            "h_ticker": row.h_ticker,
            "industry": row.industry,
            "a_close": a_close,
            "h_close": h_close,
            "fx_cnh_per_hkd": fx,
            "a_volume": a_volume,
            "h_volume": h_volume,
            "a_amount": a_volume * a_close,
            "h_amount": h_volume * h_close,
            "ex_dividend_h": ex_dividend_h,
            "single_side_halt": single_side_halt,
            "data_source": "demo_full_universe",
        }))

    output = pd.concat(records, ignore_index=True)
    output.to_csv(PRICES_FILE, index=False, encoding="utf-8-sig")
    return output


if __name__ == "__main__":
    frame = generate_demo_data(force_registry=True)
    print(f"Full-universe demo data: {frame['company_id'].nunique()} companies, {len(frame):,} rows")
