from datetime import datetime
from zoneinfo import ZoneInfo

from src.market_clock import DayOverride, get_market_state

TZ = ZoneInfo("Asia/Shanghai")


def test_overlap_and_h_only_sessions():
    day = datetime(2026, 8, 6, 10, 0, tzinfo=TZ)
    state = get_market_state(day, {day.date(): DayOverride(True, True)})
    assert state.code == "BOTH_MORNING"
    assert state.a_open and state.h_open
    assert state.watchlist_seconds == 5

    h_only = datetime(2026, 8, 6, 11, 45, tzinfo=TZ)
    state = get_market_state(h_only, {h_only.date(): DayOverride(True, True)})
    assert state.code == "H_ONLY_MORNING"
    assert not state.a_open and state.h_open


def test_calendar_override_closes_both_markets():
    day = datetime(2026, 10, 1, 10, 0, tzinfo=TZ)
    state = get_market_state(day, {day.date(): DayOverride(False, False, "holiday")})
    assert state.code == "CLOSED"
    assert state.watchlist_seconds == 0
