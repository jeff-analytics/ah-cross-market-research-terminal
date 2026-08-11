from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

import server
import src.storage.preferences as preferences


def test_default_watchlist_is_research_priority_top5(tmp_path: Path, monkeypatch):
    target = tmp_path / "watchlist.json"
    target.write_text(json.dumps({"mode": "auto_top5", "company_ids": []}), encoding="utf-8")
    monkeypatch.setattr(preferences, "WATCHLIST_FILE", target)
    # server imported the function object, but its globals still point to the preferences module.
    frame = server.get_results(include_live=False)
    expected = server._default_watchlist_ids(frame, 5)
    actual = preferences.load_watchlist(frame["company_id"].astype(str), default_count=5, default_ids=expected)
    assert len(actual) == 5
    assert actual == expected
    strict = frame[frame["company_id"].astype(str).isin(actual)].copy()
    assert strict["quality_state"].eq("可分析").all()
    assert pd.to_numeric(strict["comparability_score"], errors="coerce").ge(80).all()


def test_manual_watchlist_switches_to_custom_and_can_be_empty(tmp_path: Path, monkeypatch):
    target = tmp_path / "watchlist.json"
    monkeypatch.setattr(preferences, "WATCHLIST_FILE", target)
    preferences.save_auto_watchlist()
    assert preferences.watchlist_mode() == "auto_top5"
    assert preferences.load_watchlist(["A", "B", "C"], default_ids=["C", "B"], default_count=2) == ["C", "B"]
    preferences.save_watchlist([])
    assert preferences.watchlist_mode() == "custom"
    assert preferences.load_watchlist(["A", "B", "C"], default_ids=["C", "B"], default_count=2) == []


def test_pending_company_never_publishes_fake_zero_or_priority(monkeypatch):
    row = pd.Series({
        "company_id": "X", "company_name": "示例公司", "a_ticker": "000001.SZ", "h_ticker": "00001.HK", "industry": "测试",
        "research_state": "基准待更新", "quality_state": "可分析", "quality_reason": "实时行情可比",
        "comparability_score": 100, "analysis_status": "可分析", "comparability_reasons": "未发现明显价格问题",
        "a_contribution_pp": np.nan, "h_contribution_pp": np.nan, "fx_contribution_pp": np.nan,
        "industry_common_change_pp": np.nan, "company_residual_pp": np.nan, "premium_percentile": np.nan,
        "change_z": np.nan, "severity_score": np.nan, "display_change_pp": np.nan,
        "a_premium_pct": 12.3, "display_premium_pct": 12.3,
    })
    monkeypatch.setattr(server, "_company_row", lambda _cid: row)
    client = TestClient(server.app)
    payload = client.get('/api/company/X').json()
    assert payload["baseline_pending"] is True
    assert payload["a_contribution_pp"] is None
    assert payload["company_residual_pp"] is None
    assert payload["severity_score"] is None
    assert "未发现明显价格问题" not in payload["narrative"]
    checks = {x["label"]: x["value"] for x in payload["checks"]}
    assert checks["历史基准"] == "补抓中"
    assert checks["研究优先级"] == "待计算"


def test_frontend_null_number_guard():
    js = (server.ROOT / 'web' / 'assets' / 'app.js').read_text(encoding='utf-8')
    assert "v===null||v===undefined||v===''" in js
    assert "自动Top5" in js
