from pathlib import Path

from fastapi.testclient import TestClient

import server
from src.live_monitor import LiveMonitor
from src.storage.live_store import LiveStore

ROOT = Path(__file__).resolve().parents[1]


def test_market_page_and_controls_exist():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "web" / "assets" / "app.js").read_text(encoding="utf-8")
    for token in [
        'data-page="market"', 'id="page-market"', 'id="marketQuoteTable"',
        'id="marketCrawlNowButton"', 'id="marketSettingsButton"', 'id="marketDailyChart"',
        'id="marketViewTabs"', 'id="marketDailyRange"', 'id="marketDailyMode"',
    ]:
        assert token in html
    for token in [
        "loadMarketQuotes", "crawlMarketNow", "renderMarketDailyChart", "/api/market/quotes",
        "/api/market/", "/api/market/crawl-now", "marketSettingsButton",
    ]:
        assert token in js


def test_live_store_has_quote_detail_columns(tmp_path):
    store = LiveStore(tmp_path / "live.db")
    with store.connect() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(realtime_latest)").fetchall()}
    for name in ["a_pct_change", "h_pct_change", "a_open", "h_open", "a_high", "a_low", "h_high", "h_low"]:
        assert name in cols


def test_market_quote_api_works_with_seeded_daily_data():
    LiveMonitor()  # seeds realtime_latest from bundled daily data without making a network request
    client = TestClient(server.app)
    response = client.get("/api/market/quotes?limit=3")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] >= 1
    assert len(payload["rows"]) >= 1
    assert "premium_pct" in payload["rows"][0]
    assert "freshness" in payload["rows"][0]


def test_intraday_api_exists():
    LiveMonitor()
    client = TestClient(server.app)
    first = client.get("/api/market/quotes?limit=1").json()["rows"][0]["company_id"]
    response = client.get(f"/api/market/{first}/intraday?limit=20")
    assert response.status_code == 200
    assert "rows" in response.json()
    assert "latest" in response.json()


def test_start_launcher_checks_both_crawlers():
    text = (ROOT / "scripts" / "start_terminal.py").read_text(encoding="utf-8")
    assert "ensure_live_monitor.py" in text
    assert "ensure_daily_market_data.py" in text
    wrappers = [p for p in [ROOT/"START_TERMINAL.bat", ROOT/"START_TERMINAL.cmd", ROOT/"START_TERMINAL.command"] if p.exists()]
    assert wrappers
    for wrapper in wrappers:
        assert "start_terminal.py" in wrapper.read_text(encoding="utf-8-sig")


def test_daily_market_api_returns_daily_history():
    client = TestClient(server.app)
    first = client.get("/api/market/quotes?limit=1").json()["rows"][0]["company_id"]
    response = client.get(f"/api/market/{first}/daily?days=22")
    assert response.status_code == 200
    payload = response.json()
    assert payload["frequency"] == "1D"
    assert 1 <= len(payload["rows"]) <= 22
    assert "a_close" in payload["rows"][-1]
    assert "h_close" in payload["rows"][-1]
    assert "a_premium_pct" in payload["rows"][-1]
