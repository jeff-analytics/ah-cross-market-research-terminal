from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import server

from src.data.eastmoney import EastmoneyClient
from src.live_monitor import LiveMonitor
from src.market_clock import MarketState
from src.storage.live_store import LiveStore


def _both_open_state() -> MarketState:
    return MarketState("BOTH_MORNING", "A/H同时交易", True, True, True, 1, 3, 15, 3)


def test_last_trade_age_does_not_make_current_transport_snapshot_stale(tmp_path):
    monitor = LiveMonitor(store=LiveStore(tmp_path / "live.db"))
    pair = monitor.pairs.head(1)
    row = pair.iloc[0]
    now = datetime.now(timezone.utc)
    fetched = now.isoformat()
    quotes = {
        EastmoneyClient.a_secid(row.a_code): {
            "price": 10.0, "prev_close": 9.9, "open": 9.95, "high": 10.1, "low": 9.8,
            "quote_time": (now - timedelta(seconds=2)).isoformat(), "fetched_at": fetched,
            "provider": "tencent_http",
        },
        EastmoneyClient.h_secid(row.h_code): {
            "price": 8.0, "prev_close": 7.9, "open": 7.95, "high": 8.1, "low": 7.8,
            # Illiquid H share: last trade was five minutes ago, but the HTTP snapshot is current.
            "quote_time": (now - timedelta(minutes=5)).isoformat(), "fetched_at": fetched,
            "provider": "tencent_http",
        },
    }
    fx = {"price": 0.92, "quote_time": fetched, "fetched_at": fetched, "provider": "eastmoney_fx"}
    records, _ = monitor._make_records(pair, quotes, fx, _both_open_state(), "watchlist")
    rec = records[0]
    assert rec["quality_state"] == "实时可比"
    assert rec["premium_pct"] is not None
    assert rec["quote_skew_seconds"] == 0
    assert "H股最近成交距今" in rec["quality_reason"]
    assert "暂停" not in rec["quality_reason"]


def test_missing_intraday_high_low_is_display_note_not_premium_blocker(tmp_path):
    monitor = LiveMonitor(store=LiveStore(tmp_path / "live.db"))
    pair = monitor.pairs.head(1)
    row = pair.iloc[0]
    now = datetime.now(timezone.utc).isoformat()
    quotes = {
        EastmoneyClient.a_secid(row.a_code): {
            "price": 10.0, "prev_close": 9.9, "quote_time": now, "fetched_at": now,
            "provider": "tencent_http",
        },
        EastmoneyClient.h_secid(row.h_code): {
            "price": 8.0, "prev_close": 7.9, "quote_time": now, "fetched_at": now,
            "provider": "tencent_http",
        },
    }
    fx = {"price": 0.92, "quote_time": now, "fetched_at": now, "provider": "eastmoney_fx"}
    records, _ = monitor._make_records(pair, quotes, fx, _both_open_state(), "watchlist")
    rec = records[0]
    assert rec["quality_state"] == "实时可比"
    assert rec["premium_pct"] is not None
    assert "高低价暂缺，仅影响展示" in rec["quality_reason"]


def test_market_daily_chart_is_fixed_height_and_websocket_does_not_rerender_it():
    root = Path(__file__).resolve().parents[1]
    css = (root / "web" / "assets" / "app.css").read_text(encoding="utf-8")
    js = (root / "web" / "assets" / "app.js").read_text(encoding="utf-8")
    assert "height:390px!important" in css
    assert "max-height:390px!important" in css
    assert "flex:0 0 390px!important" in css
    assert "h=390,p={l:58,r:20,t:18,b:34}" in js
    assert "st.marketFocus=st.marketRows[i];renderMarketFocus(false);renderMarketTable()" in js


def test_completed_daily_filter_drops_intraday_partial_bar_even_if_provider_calls_it_close():
    rows = pd.DataFrame([
        {"date": "2026-08-10", "a_close": 10.0, "h_close": 8.0, "fx_cnh_per_hkd": 0.92},
        # Simulate a provider's current intraday K-line.  The field is named close,
        # but 2026-08-11 is still in session and therefore is not an official close.
        {"date": "2026-08-11", "a_close": 10.3, "h_close": 8.2, "fx_cnh_per_hkd": 0.92},
    ])
    filtered, excluded = server._completed_daily_rows(rows, pd.Timestamp("2026-08-10"))
    assert filtered["date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-08-10"]
    assert excluded == 1


def test_completed_daily_filter_requires_common_a_h_close_and_fx():
    rows = pd.DataFrame([
        {"date": "2026-08-07", "a_close": 10.0, "h_close": 8.0, "fx_cnh_per_hkd": 0.92},
        {"date": "2026-08-10", "a_close": 10.1, "h_close": None, "fx_cnh_per_hkd": 0.92},
        {"date": "2026-08-10", "a_close": 10.1, "h_close": 8.1, "fx_cnh_per_hkd": None},
    ])
    filtered, excluded = server._completed_daily_rows(rows, pd.Timestamp("2026-08-10"))
    assert filtered["date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-08-07"]
    assert excluded == 2
