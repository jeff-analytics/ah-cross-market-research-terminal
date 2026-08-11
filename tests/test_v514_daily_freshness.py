from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import server
from src.data.history_sync import latest_completed_daily_date


def _mixed_history() -> pd.DataFrame:
    demo_dates = pd.bdate_range("2025-08-07", "2026-08-05")
    demo = pd.DataFrame({
        "date": demo_dates,
        "company_id": ["TEST"] * len(demo_dates),
        "company_name": ["测试公司"] * len(demo_dates),
        "a_close": np.linspace(20, 30, len(demo_dates)),
        "h_close": np.linspace(18, 25, len(demo_dates)),
        "fx_cnh_per_hkd": 0.91,
        "a_volume": 1000,
        "h_volume": 900,
        "a_amount": 100000,
        "h_amount": 90000,
        "data_source": "demo_full_universe",
        "ex_dividend_h": 0,
        "single_side_halt": 0,
    })
    real_dates = pd.to_datetime(["2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"])
    real = pd.DataFrame({
        "date": real_dates,
        "company_id": ["TEST"] * len(real_dates),
        "company_name": ["测试公司"] * len(real_dates),
        "a_close": [30, 31, 32, 33],
        "h_close": [25, 26, 27, 28],
        "fx_cnh_per_hkd": [0.91, 0.91, 0.91, 0.91],
        "a_volume": 1000,
        "h_volume": 900,
        "a_amount": 100000,
        "h_amount": 90000,
        "data_source": "eastmoney",
        "fx_source": "ecb_reference_cross",
        "ex_dividend_h": 0,
        "single_side_halt": 0,
    })
    return pd.concat([demo, real], ignore_index=True, sort=False)


def test_expected_completed_bar_is_august_7_intraday():
    now = datetime(2026, 8, 10, 13, 31, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert latest_completed_daily_date(now).date().isoformat() == "2026-08-07"


def test_company_dashboard_history_uses_formal_rows_and_august_7(monkeypatch):
    frame = _mixed_history()
    calls = []
    monkeypatch.setattr(server, "on_demand_sync_enabled", lambda: True)
    monkeypatch.setattr(server, "ensure_company_history", lambda cid, days, full=False: calls.append((cid, days, full)) or {
        "status": "updated", "expected_through": "2026-08-07", "latest": "2026-08-07"
    })
    monkeypatch.setattr(server, "read_prices", lambda: frame.copy())
    payload = server.company_history("TEST", days=260)
    assert calls == [("TEST", 260, False)]
    assert payload["data_mode"] == "online"
    assert payload["to"].startswith("2026-08-07")
    assert payload["is_current"] is True
    assert payload["rows"][-1]["date"].startswith("2026-08-07")
    assert all(row["data_source"] == "eastmoney" for row in payload["rows"])


def test_dashboard_frontend_uses_history_metadata_not_focus_demo_source():
    js = (server.ROOT / "web" / "assets" / "app.js").read_text(encoding="utf-8")
    assert "historyMeta:{}" in js
    assert "historySourceLabel()" in js
    assert "st.historyMeta=d||{}" in js
    assert "await loadHistory();st.focus=await api('/api/company/'" in js


def test_three_year_controls_are_780_sessions():
    html = (server.ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert html.count('data-days="780">3Y</button>') >= 2
    assert 'data-days="750">3Y</button>' not in html
