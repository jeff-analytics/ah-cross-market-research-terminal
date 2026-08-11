from __future__ import annotations

from fastapi.testclient import TestClient

import server


def test_health_and_bootstrap() -> None:
    client = TestClient(server.app)
    health = client.get('/api/health')
    assert health.status_code == 200
    payload = health.json()
    assert payload['status'] == 'ok'
    assert payload['companies'] >= 100

    boot = client.get('/api/bootstrap')
    assert boot.status_code == 200
    assert boot.json()['focus_company_id']


def test_screener_quarantines_untrusted_values() -> None:
    client = TestClient(server.app)
    response = client.get('/api/screener', params={'quality': '可分析', 'limit': 250})
    assert response.status_code == 200
    rows = response.json()['rows']
    assert rows
    assert all(row['quality_state'] == '可分析' for row in rows)
    assert all(row['display_premium_pct'] is None or abs(row['display_premium_pct']) <= 300 for row in rows)


def test_company_research_endpoints() -> None:
    client = TestClient(server.app)
    company_id = client.get('/api/bootstrap').json()['focus_company_id']
    detail = client.get(f'/api/company/{company_id}')
    history = client.get(f'/api/company/{company_id}/history', params={'days': 120})
    analogs = client.get(f'/api/company/{company_id}/analogs')
    report = client.get(f'/api/company/{company_id}/report')
    assert detail.status_code == 200
    assert detail.json()['headline']
    assert history.status_code == 200 and len(history.json()['rows']) >= 30
    assert analogs.status_code == 200
    assert report.status_code == 200
    assert '异常解释卡' in report.text


def test_watchlist_round_trip() -> None:
    client = TestClient(server.app)
    initial = client.get('/api/watchlist').json()['company_ids']
    assert initial
    response = client.post('/api/watchlist', json={'company_ids': initial})
    assert response.status_code == 200
    assert response.json()['company_ids'] == initial
