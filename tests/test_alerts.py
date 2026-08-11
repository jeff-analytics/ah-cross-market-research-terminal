from src.analysis.alerts import build_alerts
from src.analysis.screener import build_screener
from src.data.demo import generate_demo_data


def test_alert_queue_contains_high_priority_or_quality_flags():
    prices = generate_demo_data()
    screen, _ = build_screener(prices)
    alerts = build_alerts(screen, severity_threshold=55, absolute_change_threshold=1.5)
    assert not alerts.empty
    assert {"urgency", "trigger", "recommended_action"}.issubset(alerts.columns)
    assert alerts["company_name"].notna().all()


def test_watchlist_only_filter():
    prices = generate_demo_data()
    screen, _ = build_screener(prices)
    target = screen.iloc[0]["company_id"]
    alerts = build_alerts(
        screen,
        watchlist=[target],
        severity_threshold=0,
        absolute_change_threshold=0,
        watchlist_only=True,
    )
    assert set(alerts["company_id"]) == {target}
