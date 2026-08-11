from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import server
from src.data.history_sync import latest_completed_daily_date


def test_latest_completed_daily_date_excludes_intraday_bar():
    now = datetime(2026, 8, 10, 11, 5, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert latest_completed_daily_date(now).date().isoformat() == "2026-08-07"


def test_latest_completed_daily_date_includes_today_after_both_markets_close():
    now = datetime(2026, 8, 10, 16, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert latest_completed_daily_date(now).date().isoformat() == "2026-08-10"


def _history_frame(rows: int = 1000) -> pd.DataFrame:
    dates = pd.bdate_range("2022-01-03", periods=rows)
    a = np.linspace(20, 60, rows)
    h = np.linspace(18, 55, rows)
    fx = np.full(rows, 0.91)
    return pd.DataFrame({
        "date": dates,
        "company_id": ["TEST"] * rows,
        "company_name": ["测试公司"] * rows,
        "a_close": a,
        "h_close": h,
        "fx_cnh_per_hkd": fx,
        "a_volume": np.full(rows, 1_000_000),
        "h_volume": np.full(rows, 800_000),
        "a_amount": a * 1_000_000,
        "h_amount": h * 800_000,
        "data_source": ["eastmoney"] * rows,
        "ex_dividend_h": np.zeros(rows, dtype=int),
        "single_side_halt": np.zeros(rows, dtype=int),
    })


def test_all_range_is_not_a_5000_day_alias(monkeypatch):
    frame = _history_frame(1000)
    monkeypatch.setattr(server, "read_prices", lambda: frame.copy())
    monkeypatch.setattr(server, "on_demand_sync_enabled", lambda: False)

    three_year = server.market_daily("TEST", 780)
    all_history = server.market_daily("TEST", 0)

    assert three_year["count"] == 780
    assert all_history["count"] == 1000
    assert all_history["range"] == "all"
    assert all_history["requested_days"] is None
    assert all_history["history_start_basis"] == "first_common_ah_trading_day"


def test_market_html_uses_true_all_range_sentinel():
    html = (server.ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'data-days="0">全部</button>' in html
    assert 'data-days="5000">全部</button>' not in html


def test_partial_daily_crawl_does_not_mark_market_current(tmp_path, monkeypatch):
    import scripts.ensure_daily_market_data as ensure
    payload = {
        "status": "success",
        "requested_pairs": 202,
        "updated_pairs": 201,
        "failed_pairs": [{"company_id": "X"}],
        "end": "20260807",
    }
    log = tmp_path / "update_log.json"
    log.write_text(__import__("json").dumps(payload), encoding="utf-8")
    monkeypatch.setattr(ensure, "UPDATE_LOG", log)
    assert ensure.last_success_date() is None
