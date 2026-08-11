from pathlib import Path


def test_global_freshness_functions_exist():
    text = Path("src/data/history_sync.py").read_text(encoding="utf-8")
    assert "def market_history_audit()" in text
    assert "def sync_market_recent_history(" in text
    assert "stale_company_ids" in text


def test_startup_daily_check_is_whole_universe_and_blocking():
    text = Path("scripts/ensure_daily_market_data.py").read_text(encoding="utf-8")
    assert "market_history_audit" in text
    assert "sync_market_recent_history" in text
    assert "whole_universe" in text
    assert "subprocess.Popen" not in text


def test_market_update_does_not_certify_lagging_responses():
    text = Path("src/data/loader.py").read_text(encoding="utf-8")
    assert "lagging_pairs" in text
    assert "and not lagging_pairs" in text


def test_daily_status_endpoint_is_universe_wide():
    text = Path("server.py").read_text(encoding="utf-8")
    assert '/api/market/daily-status' in text
    assert "market_history_audit()" in text
