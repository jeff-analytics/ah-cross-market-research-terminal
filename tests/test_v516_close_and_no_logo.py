from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from src.data.eastmoney import EastmoneyClient
from src.market_clock import TZ, get_market_state

ROOT = Path(__file__).resolve().parents[1]


def _leg_frame(date: str, close: float, change: float):
    return pd.DataFrame([
        {
            "date": pd.Timestamp(date),
            "open": close - 0.2,
            "close": close,
            "high": close + 0.3,
            "low": close - 0.4,
            "volume": 123400.0,
            "amount": 5678000.0,
            "amplitude": 1.0,
            "pct_change": 2.0,
            "change": change,
            "turnover": 0.5,
        }
    ])


def test_daily_pair_preserves_close_display_fields(monkeypatch):
    client = EastmoneyClient(save_raw=False)

    def fake_fetch_kline(secid, start, end, market):
        return _leg_frame("2026-08-10", 13.48 if market == "A" else 8.65, 0.28)

    monkeypatch.setattr(client, "fetch_kline", fake_fetch_kline)
    monkeypatch.setattr("src.data.eastmoney.time.sleep", lambda *_: None)
    pair = client.fetch_pair("601038", "00038", "20260810", "20260810")
    required = {
        "a_open", "a_close", "a_high", "a_low", "a_prev_close", "a_pct_change", "a_change",
        "h_open", "h_close", "h_high", "h_low", "h_prev_close", "h_pct_change", "h_change",
    }
    assert required.issubset(pair.columns)
    assert pair.loc[0, "a_prev_close"] == pytest.approx(13.20)
    assert pair.loc[0, "h_prev_close"] == pytest.approx(8.37)


def test_after_close_refresh_and_no_logo_assets():
    state = get_market_state(datetime(2026, 8, 10, 17, 0, tzinfo=TZ))
    assert state.premium_mode == "close"
    assert state.a_session == "closed"
    assert state.h_session == "closed"
    assert state.universe_seconds > 0
    assert state.fx_open is True

    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "web" / "assets" / "app.js").read_text(encoding="utf-8")
    assert "app-icon" not in html
    assert "brand-box" not in html
    assert "companyLogoHtml" not in js
    assert not (ROOT / "web" / "assets" / "company-logos").exists()
