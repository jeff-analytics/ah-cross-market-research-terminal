from datetime import datetime, timedelta, timezone

import pandas as pd

from src.data.eastmoney import EastmoneyClient
from src.data.realtime import TencentRealtimeClient, SinaARealtimeClient
from src.live_monitor import LiveMonitor, QueueDue
from src.market_clock import MarketState
from src.storage.live_store import LiveStore
from src.storage.refresh_policy import RefreshPolicy, save_refresh_policy, load_refresh_policy


def _tencent_line(symbol: str, code: str, name: str, stamp: str, price: str, prev: str, open_: str, high: str, low: str, pct: str = "1.00") -> str:
    fields = [""] * 39
    fields[0] = "51"
    fields[1] = name
    fields[2] = code
    fields[3] = price
    fields[4] = prev
    fields[5] = open_
    fields[6] = "123456"
    fields[11] = "1000000"
    fields[30] = stamp
    fields[31] = str(float(price) - float(prev))
    fields[32] = pct
    fields[33] = high
    fields[34] = low
    fields[37] = "987654321"
    return f'v_{symbol}="' + "~".join(fields) + '";'


def test_tencent_parser_preserves_exchange_timestamp_and_ohlc():
    fetched = datetime(2026, 8, 7, 5, 15, 2, tzinfo=timezone.utc)  # 13:15:02 Beijing
    line = _tencent_line("sh600000", "600000", "浦发银行", "20260807131501", "10.20", "10.00", "10.01", "10.30", "9.95", "2.00")
    parsed = TencentRealtimeClient._parse_line(line, fetched)
    assert parsed is not None
    key, quote = parsed
    assert key == EastmoneyClient.a_secid("600000")
    assert quote["price"] == 10.20
    assert quote["high"] == 10.30
    assert quote["low"] == 9.95
    assert quote["provider"] == "tencent_http"
    assert datetime.fromisoformat(quote["quote_time"]).astimezone(timezone.utc) == datetime(2026, 8, 7, 5, 15, 1, tzinfo=timezone.utc)


def test_open_market_never_mixes_daily_cache_with_one_live_leg(tmp_path):
    store = LiveStore(tmp_path / "live.db")
    monitor = LiveMonitor(store=store)
    pair = monitor.pairs.head(1)
    row = pair.iloc[0]
    now = datetime.now(timezone.utc).isoformat()
    quotes = {
        # Deliberately omit A leg; only H is live.
        EastmoneyClient.h_secid(row.h_code): {
            "price": 8.0, "prev_close": 7.9, "high": 8.1, "low": 7.8,
            "quote_time": now, "provider": "tencent_http",
        }
    }
    fx = {"price": 0.92, "quote_time": now, "provider": "eastmoney_fx"}
    state = MarketState("BOTH_AFTERNOON", "A/H同时交易", True, True, True, 1, 3, 15, 10)
    records, snapshots = monitor._make_records(pair, quotes, fx, state, "watchlist")
    assert len(records) == 1
    assert records[0]["quality_state"] == "暂停计算"
    assert records[0]["premium_pct"] is None
    assert records[0]["a_price"] is None
    assert records[0]["stale_flag"] == 1
    assert "A股" in records[0]["quality_reason"]
    assert not snapshots


def test_cross_market_timestamp_skew_pauses_premium(tmp_path):
    store = LiveStore(tmp_path / "live.db")
    monitor = LiveMonitor(store=store)
    pair = monitor.pairs.head(1)
    row = pair.iloc[0]
    now = datetime.now(timezone.utc)
    quotes = {
        EastmoneyClient.a_secid(row.a_code): {
            "price": 10.0, "prev_close": 9.9, "high": 10.1, "low": 9.8,
            "quote_time": now.isoformat(), "provider": "tencent_http",
        },
        EastmoneyClient.h_secid(row.h_code): {
            "price": 8.0, "prev_close": 7.9, "high": 8.1, "low": 7.8,
            "quote_time": (now - timedelta(seconds=45)).isoformat(), "provider": "tencent_http",
        },
    }
    fx = {"price": 0.92, "quote_time": now.isoformat(), "provider": "eastmoney_fx"}
    state = MarketState("BOTH_AFTERNOON", "A/H同时交易", True, True, True, 1, 3, 15, 10)
    records, _ = monitor._make_records(pair, quotes, fx, state, "watchlist")
    assert records[0]["premium_pct"] is None
    assert records[0]["quality_state"] == "暂停计算"
    assert "时间错位" in records[0]["quality_reason"] or "延迟" in records[0]["quality_reason"]


def test_high_frequency_policy_accepts_1_3_15_seconds(tmp_path):
    path = tmp_path / "refresh.json"
    save_refresh_policy(RefreshPolicy(True, 1, 3, 15, 3), path)
    policy = load_refresh_policy(path)
    assert policy.enabled is True
    assert (policy.watchlist_seconds, policy.priority_seconds, policy.universe_seconds, policy.status_seconds) == (1, 3, 15, 3)


def test_sina_parser_preserves_a_share_exchange_time_and_ohlc():
    fetched = datetime(2026, 8, 7, 5, 18, 2, tzinfo=timezone.utc)
    fields = ["浦发银行", "10.01", "10.00", "10.20", "10.30", "9.95", "0", "0", "123456", "987654321"]
    fields += ["0"] * 20
    fields += ["2026-08-07", "13:18:01", "00"]
    line = 'var hq_str_sh600000="' + ",".join(fields) + '";'
    parsed = SinaARealtimeClient._parse_line(line, fetched)
    assert parsed is not None
    key, quote = parsed
    assert key == EastmoneyClient.a_secid("600000")
    assert quote["provider"] == "sina_http"
    assert quote["price"] == 10.20
    assert quote["high"] == 10.30
    assert quote["low"] == 9.95
    assert datetime.fromisoformat(quote["quote_time"]).astimezone(timezone.utc) == datetime(2026, 8, 7, 5, 18, 1, tzinfo=timezone.utc)


def test_reported_mixed_time_failure_is_quarantined(tmp_path):
    """Regression for the 2026-08-07 screenshot: old A cache + current H + stale FX."""
    store = LiveStore(tmp_path / "live.db")
    monitor = LiveMonitor(store=store)
    pair = monitor.pairs.head(1)
    row = pair.iloc[0]
    now = datetime.now(timezone.utc)
    # No A quote at all: old daily cache must not be substituted while A is open.
    quotes = {
        EastmoneyClient.h_secid(row.h_code): {
            "price": 3.95, "prev_close": 4.075, "high": 4.075, "low": 3.905,
            "quote_time": now.isoformat(), "provider": "tencent_http",
        }
    }
    fx = {"price": 0.92, "quote_time": (now - timedelta(hours=4)).isoformat(), "provider": "eastmoney_fx"}
    state = MarketState("BOTH_AFTERNOON", "A/H同时交易", True, True, True, 1, 3, 15, 3)
    records, snapshots = monitor._make_records(pair, quotes, fx, state, "watchlist")
    rec = records[0]
    assert rec["a_price"] is None
    assert rec["premium_pct"] is None
    assert rec["premium_change_pp"] is None
    assert rec["quality_state"] == "暂停计算"
    assert "交易中的市场缺少实时行情" in rec["quality_reason"]
    assert "汇率延迟" in rec["quality_reason"]
    assert snapshots == []


def test_bundled_demo_history_is_not_used_as_live_change_baseline(tmp_path):
    monitor = LiveMonitor(store=LiveStore(tmp_path / "live.db"))
    # The packaged prices.csv is deterministic demo data; v4.4 must not use it
    # to calculate a live "today change" against real intraday quotes.
    assert monitor.daily_baseline == {}


def test_hybrid_router_uses_sina_a_fallback_and_never_eastmoney_stock_fallback(monkeypatch):
    from src.data.realtime import HybridRealtimeClient

    pairs = pd.DataFrame([{"a_code": "600000", "h_code": "00005"}])
    client = HybridRealtimeClient(timeout=0.1)
    now = datetime.now(timezone.utc).isoformat()

    class T:
        def fetch_a(self, codes): return {}
        def fetch_h(self, codes):
            return {EastmoneyClient.h_secid("00005"): {"price": 50.0, "high": 51.0, "low": 49.0, "quote_time": now, "provider": "tencent_http"}}
    class S:
        def fetch_a(self, codes):
            return {EastmoneyClient.a_secid("600000"): {"price": 10.0, "high": 10.1, "low": 9.9, "quote_time": now, "provider": "sina_http"}}
    class F:
        last_error = "not configured"
        def available(self): return False
    class E:
        def fetch_secids(self, secids):
            raise AssertionError("Eastmoney must not be used as stock-leg fallback in v4.4")
        def fetch_fx(self): return {"price": 0.92, "quote_time": now, "provider": "eastmoney_fx"}

    client.tencent, client.sina, client.futu, client.eastmoney = T(), S(), F(), E()
    quotes, fx = client.fetch_pair_quotes(pairs, True, True, True)
    assert quotes[EastmoneyClient.a_secid("600000")]["provider"] == "sina_http"
    assert quotes[EastmoneyClient.h_secid("00005")]["provider"] == "tencent_http"
    assert fx["provider"] == "eastmoney_fx"
