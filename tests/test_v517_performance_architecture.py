from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

import server
from src.data.realtime import ProviderHealthRegistry
from src.runtime_cache import MemoryQuoteCache, ServerLiveCache
from src.storage.live_store import AsyncLiveWriter, LiveStore
from src.storage.provider_settings import ProviderSettings


def _row(company_id: str = "AH_TEST_00001") -> dict:
    return {
        "company_id": company_id,
        "company_name": "性能测试",
        "a_code": "000001.SZ",
        "h_code": "00001.HK",
        "industry": "测试",
        "fetched_at": "2026-08-11T01:30:00+00:00",
        "a_price": 10.0,
        "h_price": 8.0,
        "fx_cnh_per_hkd": 0.92,
        "premium_pct": 35.87,
        "stale_flag": 0,
        "updated_queue": "focus",
    }


def test_memory_quote_cache_updates_without_sqlite_roundtrip():
    cache = MemoryQuoteCache([_row()])
    assert cache.get("AH_TEST_00001")["a_price"] == 10.0
    updated = _row()
    updated["a_price"] = 10.5
    cache.update([updated])
    assert cache.get("AH_TEST_00001")["a_price"] == 10.5
    assert cache.meta()["revision"] >= 2


def test_async_sqlite_writer_persists_and_bumps_revision(tmp_path: Path):
    store = LiveStore(tmp_path / "live.db")
    before = store.get_revision()
    writer = AsyncLiveWriter(store, flush_interval=0.01)
    try:
        writer.submit_latest([_row()])
        assert writer.flush(timeout=2.0)
        saved = store.latest_map(["AH_TEST_00001"])["AH_TEST_00001"]
        assert saved["a_price"] == 10.0
        assert store.get_revision() != before
        assert writer.metrics()["written_latest"] == 1
    finally:
        writer.close(timeout=2.0)


def test_server_cache_reloads_only_after_revision_changes(tmp_path: Path):
    store = LiveStore(tmp_path / "live.db")
    store.upsert_latest([_row()])
    cache = ServerLiveCache(store, min_check_interval=0.05)
    assert cache.get("AH_TEST_00001")["a_price"] == 10.0
    changed = _row()
    changed["a_price"] = 11.0
    store.upsert_latest([changed])
    time.sleep(0.06)
    assert cache.get("AH_TEST_00001")["a_price"] == 11.0


def test_provider_health_opens_circuit_after_consecutive_failures():
    registry = ProviderHealthRegistry()
    settings = ProviderSettings(
        circuit_failure_threshold=2,
        circuit_cooldown_seconds=2,
        fast_retry_attempts=0,
        retry_jitter_min_ms=0,
        retry_jitter_max_ms=0,
    )
    calls = {"n": 0}

    def fail():
        calls["n"] += 1
        raise RuntimeError("provider down")

    for _ in range(2):
        try:
            registry.call("test_provider", fail, settings)
        except RuntimeError:
            pass
    assert registry.snapshot()["test_provider"]["circuit_state"] == "open"
    before = calls["n"]
    try:
        registry.call("test_provider", fail, settings)
    except RuntimeError as exc:
        assert "circuit open" in str(exc)
    assert calls["n"] == before


def test_focused_quote_api_and_websocket_push():
    client = TestClient(server.app)
    bootstrap = client.get("/api/bootstrap").json()
    company_id = bootstrap["focus_company_id"]
    response = client.get(f"/api/market/quote/{company_id}")
    assert response.status_code == 200
    assert response.json()["company_id"] == company_id
    focus = client.post(f"/api/live/focus/{company_id}")
    assert focus.status_code == 200
    with client.websocket_connect(f"/ws/live/{company_id}") as websocket:
        payload = websocket.receive_json()
        assert payload["type"] == "quote"
        assert payload["company_id"] == company_id
        assert isinstance(payload["row"], dict)
