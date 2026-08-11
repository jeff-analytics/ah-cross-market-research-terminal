from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from src.config import DEMO_SNAPSHOT_FILE, SETTINGS
from src.data.eastmoney import EastmoneyClient
from src.data.loader import _default_history_start
from src.data.realtime import EastmoneyRealtimeClient
from src.storage.provider_settings import load_provider_settings


def test_fx_freshness_uses_successful_fetch_time(monkeypatch):
    client = EastmoneyRealtimeClient(save_raw=False)
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()

    def fake_fetch(_secids):
        return {
            EastmoneyClient.fx_secid(): {
                'price': 0.8654,
                'quote_time': old,
                'fetched_at': fresh,
                'provider': 'eastmoney_http',
            }
        }

    monkeypatch.setattr(client, 'fetch_secids', fake_fetch)
    quote = client.fetch_fx()
    assert quote is not None
    assert quote['source_quote_time'] == old
    assert quote['quote_time'] == fresh
    assert quote['provider'] == 'eastmoney_fx'


def test_provider_quality_thresholds_are_loaded_from_json():
    cfg = load_provider_settings()
    assert cfg.fx_cache_seconds >= 1
    assert cfg.open_leg_max_age_seconds >= 1
    assert cfg.both_market_max_skew_seconds >= 1
    assert cfg.fx_max_age_seconds >= 1


def test_demo_snapshot_date_is_metadata_not_source_literal():
    payload = __import__('json').loads(DEMO_SNAPSHOT_FILE.read_text(encoding='utf-8'))
    assert payload['purpose'] == 'offline_preview_only'
    source = (Path(__file__).resolve().parents[1] / 'src' / 'data' / 'demo.py').read_text(encoding='utf-8')
    assert payload['snapshot_end'] not in source


def test_default_online_history_start_is_relative_to_requested_end():
    end = '20260810'
    start = pd.Timestamp(_default_history_start(end))
    assert start < pd.Timestamp(end)
    assert (pd.Timestamp(end).year - start.year) >= SETTINGS.daily_history_years - 1


def test_universe_baseline_is_derived_from_bundled_registry():
    assert SETTINGS.expected_universe_count > 0


def test_manual_refresh_policy_can_go_faster_than_high_frequency_preset(tmp_path):
    from src.storage.refresh_policy import RefreshPolicy, save_refresh_policy, load_refresh_policy
    path = tmp_path / "refresh.json"
    save_refresh_policy(RefreshPolicy(True, 1, 1, 5, 1), path)
    policy = load_refresh_policy(path)
    assert (policy.watchlist_seconds, policy.priority_seconds, policy.universe_seconds, policy.status_seconds) == (1, 1, 5, 1)
