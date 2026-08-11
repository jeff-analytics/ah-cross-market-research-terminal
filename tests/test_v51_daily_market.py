from pathlib import Path
from fastapi.testclient import TestClient
import server

ROOT = Path(__file__).resolve().parents[1]


def test_market_center_has_three_quote_views_and_daily_only_controls():
    html = (ROOT / 'web' / 'index.html').read_text(encoding='utf-8')
    for token in [
        'data-market-view="ah"', 'data-market-view="a"', 'data-market-view="h"',
        'id="marketDailyChart"', 'id="marketDailyRange"', 'id="marketDailyMode"',
        'data-daily-mode="compare"', 'data-daily-mode="a"', 'data-daily-mode="h"',
        'data-daily-mode="premium"',
    ]:
        assert token in html
    assert '周线' not in html
    assert '月线' not in html
    assert '分钟K' not in html


def test_daily_market_api_respects_daily_window_sizes():
    client = TestClient(server.app)
    first = client.get('/api/market/quotes?limit=1').json()['rows'][0]['company_id']
    for days in [5, 22, 66, 132, 260]:
        payload = client.get(f'/api/market/{first}/daily?days={days}').json()
        assert payload['frequency'] == '1D'
        assert 1 <= payload['count'] <= days
        assert len(payload['rows']) == payload['count']


def test_daily_market_api_exposes_both_a_and_h_history():
    client = TestClient(server.app)
    first = client.get('/api/market/quotes?limit=1').json()['rows'][0]['company_id']
    payload = client.get(f'/api/market/{first}/daily?days=22').json()
    row = payload['rows'][-1]
    for key in ['date','a_close','h_close','h_price_cny','fx_cnh_per_hkd','a_premium_pct','a_amount','h_amount']:
        assert key in row


def test_market_js_binds_view_range_mode_and_hover():
    js = (ROOT / 'web' / 'assets' / 'app.js').read_text(encoding='utf-8')
    for token in [
        "#marketViewTabs [data-market-view]",
        "#marketDailyRange [data-days]",
        "#marketDailyMode [data-daily-mode]",
        'loadMarketDaily', 'renderMarketDailyChart', 'marketDailyHover', 'onmousemove',
    ]:
        assert token in js
