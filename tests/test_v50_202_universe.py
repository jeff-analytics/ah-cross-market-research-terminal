from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import server
from src.data.pairs import load_pairs, universe_status
from src.live_monitor import LiveMonitor
from src.storage.live_store import LiveStore


def test_validated_universe_has_exactly_202_unique_pairs():
    pairs = load_pairs(active_only=True)
    assert len(pairs) == 202
    assert pairs['a_code'].nunique() == 202
    assert pairs['h_code'].nunique() == 202


def test_recent_additions_are_in_bundled_full_universe():
    pairs = load_pairs(active_only=True)
    expected = {
        '002475','002487','002600','300408','300476','300568','300661','300866',
        '301087','301377','601678','603296','688062','688249','688337','688630','300308',
    }
    assert expected.issubset(set(pairs['a_code']))
    zhongji = pairs[pairs['a_code'].eq('300308')].iloc[0]
    assert zhongji['h_code'] == '03308'


def test_universe_status_is_full_ready():
    status = universe_status()
    assert status['target_count'] == 202
    assert status['active_count'] == 202
    assert status['full_ready'] is True


def test_live_store_represents_every_pair_even_before_online_quotes(tmp_path: Path):
    store = LiveStore(tmp_path / 'live.db')
    monitor = LiveMonitor(store=store)
    latest = monitor.store.read_latest()
    assert len(latest) == 202
    assert latest['company_id'].nunique() == 202


def test_health_and_search_report_full_universe():
    client = TestClient(server.app)
    health = client.get('/api/health').json()
    assert health['companies'] == 202
    assert health['target_companies'] == 202
    search = client.get('/api/companies/search', params={'q':'中际旭创'}).json()
    assert search['rows']
    assert search['rows'][0]['a_ticker'] == '300308.SZ'


def test_new_pair_is_available_in_company_research():
    client = TestClient(server.app)
    r = client.get('/api/company/AH_300308_03308/history')
    assert r.status_code == 200
    payload = r.json()
    assert 'rows' in payload
