from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Smoke tests validate local packaging/API behavior and must not wait on public
# market-data endpoints. Production launchers leave on-demand sync enabled.
os.environ.setdefault("AH_ON_DEMAND_HISTORY_SYNC", "0")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

import server

client = TestClient(server.app)
checks = {
    'health': client.get('/api/health'),
    'bootstrap': client.get('/api/bootstrap'),
    'summary': client.get('/api/summary'),
    'screener': client.get('/api/screener', params={'quality': '可分析', 'limit': 5}),
    'watchlist': client.get('/api/watchlist'),
    'data_quality': client.get('/api/data-quality'),
}
for name, response in checks.items():
    if response.status_code != 200:
        raise SystemExit(f'{name} failed: HTTP {response.status_code} {response.text[:300]}')

company_id = checks['bootstrap'].json()['focus_company_id']
for path in [f'/api/company/{company_id}', f'/api/company/{company_id}/history?days=120', f'/api/company/{company_id}/analogs']:
    response = client.get(path)
    if response.status_code != 200:
        raise SystemExit(f'{path} failed: HTTP {response.status_code} {response.text[:300]}')

print('Professional terminal smoke test passed.')
print(f"Companies: {checks['health'].json()['companies']}")
print(f"Price rows: {checks['health'].json()['prices']}")
print(f"Analyzable: {checks['summary'].json()['analyzable']}")
