from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src.data.eastmoney import EastmoneyClient
from src.live_monitor import LiveMonitor
from src.market_clock import DayOverride, MarketState, get_market_state
from src.storage.live_store import LiveStore

TZ = ZoneInfo("Asia/Shanghai")


def _at(hour: int, minute: int) -> datetime:
    return datetime(2026, 8, 10, hour, minute, tzinfo=TZ)


def test_market_clock_distinguishes_sync_single_lunch_auction_and_close():
    overrides = {_at(10, 0).date(): DayOverride(True, True)}

    s = get_market_state(_at(10, 0), overrides)
    assert s.premium_mode == "realtime_sync"
    assert s.a_open and s.h_open

    s = get_market_state(_at(11, 45), overrides)
    assert s.premium_mode == "indicative_single_leg"
    assert not s.a_open and s.h_open
    assert s.a_session == "break"

    s = get_market_state(_at(12, 30), overrides)
    assert s.premium_mode == "lunch_snapshot"
    assert not s.any_open

    s = get_market_state(_at(14, 58), overrides)
    assert s.premium_mode == "auction_indicative"
    assert s.a_session == "auction"
    assert s.h_session == "open"

    s = get_market_state(_at(15, 30), overrides)
    assert s.premium_mode == "indicative_single_leg"
    assert not s.a_open and s.h_open
    assert s.a_session == "closed"

    s = get_market_state(_at(16, 15), overrides)
    assert s.premium_mode == "close"
    assert not s.any_open


def test_market_clock_handles_one_market_holiday_without_false_both_open():
    day = _at(10, 0).date()
    a_only = get_market_state(_at(10, 0), {day: DayOverride(True, False, "HK holiday")})
    assert a_only.code.startswith("A_ONLY")
    assert a_only.premium_mode == "indicative_single_leg"
    assert a_only.a_open and not a_only.h_open

    h_only = get_market_state(_at(10, 0), {day: DayOverride(False, True, "CN holiday")})
    assert h_only.code.startswith("H_ONLY")
    assert h_only.premium_mode == "indicative_single_leg"
    assert not h_only.a_open and h_only.h_open


def test_preopen_uses_previous_close_mode_and_not_live_flags():
    day = _at(9, 10).date()
    s = get_market_state(_at(9, 10), {day: DayOverride(True, True)})
    assert s.code == "PRE_OPEN"
    assert s.premium_mode == "previous_close"
    assert not s.a_open and not s.h_open


def test_one_side_valid_quotes_are_labelled_indicative_not_realtime(tmp_path):
    store = LiveStore(tmp_path / "live.db")
    monitor = LiveMonitor(store=store)
    pair = monitor.pairs.head(1)
    row = pair.iloc[0]
    now = datetime.now(timezone.utc).isoformat()
    quotes = {
        EastmoneyClient.a_secid(row.a_code): {
            "price": 10.0, "prev_close": 9.9, "open": 9.95, "high": 10.1, "low": 9.8,
            "quote_time": now, "provider": "tencent_http",
        },
        EastmoneyClient.h_secid(row.h_code): {
            "price": 8.0, "prev_close": 7.9, "open": 7.95, "high": 8.1, "low": 7.8,
            "quote_time": now, "provider": "tencent_http",
        },
    }
    fx = {"price": 0.92, "fetched_at": now, "quote_time": now, "provider": "eastmoney_fx"}
    state = MarketState(
        "H_ONLY_AFTERNOON", "H股单边交易 · A股已收盘", False, True, True, 10, 20, 60, 30,
        premium_mode="indicative_single_leg", a_session="closed", h_session="open",
        a_trading_day=True, h_trading_day=True,
    )
    records, _ = monitor._make_records(pair, quotes, fx, state, "watchlist")
    rec = records[0]
    assert rec["quality_state"] == "单边指示"
    assert rec["premium_pct"] is not None
    assert rec["a_contribution_pp"] is None
    assert rec["h_contribution_pp"] is None
    assert rec["fx_contribution_pp"] is None


def test_synchronized_premium_is_carried_into_later_reference_state(tmp_path):
    store = LiveStore(tmp_path / "live.db")
    monitor = LiveMonitor(store=store)
    cid = monitor.pairs["company_id"].iloc[0]
    store.upsert_latest([{
        "company_id": cid,
        "company_name": monitor.pairs.iloc[0]["company_name"],
        "a_code": monitor.pairs.iloc[0]["a_code"],
        "h_code": monitor.pairs.iloc[0]["h_code"],
        "industry": monitor.pairs.iloc[0]["industry"],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "premium_pct": 25.0,
        "sync_premium_pct": 25.0,
        "sync_snapshot_time": datetime.now(timezone.utc).isoformat(),
        "quality_state": "实时可比",
        "stale_flag": 0,
    }])
    stored = store.latest_map([cid])[cid]
    assert stored["sync_premium_pct"] == 25.0
    assert stored["sync_snapshot_time"] is not None
