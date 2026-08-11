from datetime import datetime, timezone

from src.storage.live_store import LiveStore


def _record(company_id="AH_TEST"):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "company_id": company_id,
        "company_name": "测试公司",
        "a_code": "600000",
        "h_code": "00001",
        "industry": "测试",
        "fetched_at": now,
        "a_quote_time": now,
        "h_quote_time": now,
        "fx_quote_time": now,
        "a_price": 10.0,
        "h_price": 8.0,
        "fx_cnh_per_hkd": 0.92,
        "premium_pct": 35.87,
        "premium_change_pp": 1.2,
        "a_contribution_pp": 0.5,
        "h_contribution_pp": 0.6,
        "fx_contribution_pp": 0.1,
        "a_volume": 100,
        "h_volume": 90,
        "a_amount": 1000,
        "h_amount": 900,
        "market_state": "BOTH_MORNING",
        "source": "test",
        "data_age_seconds": 1,
        "stale_flag": 0,
        "updated_queue": "watchlist",
    }


def test_live_store_upsert_and_snapshot(tmp_path):
    store = LiveStore(tmp_path / "live.db")
    record = _record()
    store.upsert_latest([record])
    latest = store.read_latest()
    assert len(latest) == 1
    record["premium_pct"] = 36.5
    store.upsert_latest([record])
    assert float(store.read_latest().iloc[0]["premium_pct"]) == 36.5

    store.insert_snapshots([{
        "company_id": "AH_TEST",
        "snapshot_time": record["fetched_at"],
        "snapshot_bucket": record["fetched_at"],
        "a_price": 10.0,
        "h_price": 8.0,
        "fx_cnh_per_hkd": 0.92,
        "premium_pct": 35.87,
        "premium_change_pp": 1.2,
        "market_state": "BOTH_MORNING",
        "trigger_reason": "watchlist",
        "source": "test",
    }])
    assert len(store.read_snapshots("AH_TEST")) == 1
