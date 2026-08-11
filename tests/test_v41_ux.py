from pathlib import Path

from src.market_clock import get_market_state
from src.storage.refresh_policy import RefreshPolicy, apply_refresh_policy

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web/index.html").read_text(encoding="utf-8")
JS = (ROOT / "web/assets/app.js").read_text(encoding="utf-8")
CSS = (ROOT / "web/assets/app.css").read_text(encoding="utf-8")


def test_sidebar_shortcut_numbers_removed():
    for digit in range(1, 8):
        assert f"<kbd>{digit}</kbd>" not in HTML


def test_chart_hover_is_implemented():
    assert "chart-tooltip" in JS
    assert "el.onmousemove" in JS
    assert "hover-line" in JS
    assert ".chart-tooltip.show" in CSS


def test_date_only_renderer_removes_midnight_suffix():
    assert "function dateOnly" in JS
    assert "T00:00:00" in JS
    assert "dateOnly(r.date)" in JS


def test_custom_refresh_controls_and_api_client_exist():
    for control in [
        "customRefreshToggle",
        "refreshWatchSeconds",
        "refreshPrioritySeconds",
        "refreshUniverseSeconds",
        "refreshStatusSeconds",
    ]:
        assert f'id="{control}"' in HTML
    assert "/api/live/refresh-policy" in JS


def test_refresh_policy_overrides_trading_state():
    state = get_market_state()
    if not state.any_open:
        # Create the same shape with an open state when the test happens off-hours.
        from src.market_clock import MarketState
        state = MarketState("TEST", "test", True, True, True, 5, 15, 60, 30)
    custom = apply_refresh_policy(state, RefreshPolicy(True, 7, 21, 75, 20))
    assert custom.watchlist_seconds == 7
    assert custom.priority_seconds == 21
    assert custom.universe_seconds == 75


def test_collapsed_sidebar_has_safe_width():
    assert ".sidebar.collapsed{width:72px" in CSS
    assert ".sidebar.collapsed .nav-item{width:48px" in CSS


def test_redundant_bilingual_breadcrumb_removed():
    for text in ["Research Dashboard", "Equity Screener", "Company Snapshot", "Historical Validation", "Data Quality", "System Status"]:
        assert text not in HTML
