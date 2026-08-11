from datetime import datetime, timezone

from src.data.eastmoney import EastmoneyClient
from src.live_monitor import LiveMonitor, QueueDue
from src.market_clock import MarketState
from src.storage.live_store import LiveStore


class RecordingClient:
    def __init__(self):
        self.calls = []
        self.bump = 0

    def fetch_pair_quotes(self, pairs, update_a, update_h, update_fx, batch_size=40):
        self.calls.append((bool(update_a), bool(update_h), bool(update_fx), len(pairs)))
        self.bump += 1
        now = datetime.now(timezone.utc).isoformat()
        quotes = {}
        for row in pairs.itertuples(index=False):
            quotes[EastmoneyClient.a_secid(row.a_code)] = {
                'price': 10.0, 'volume': 1000, 'amount': 10000,
                'pct_change': 1.0, 'change': 0.1, 'prev_close': 9.9,
                'high': 10.1, 'low': 9.8, 'quote_time': now, 'provider': 'tencent_http',
            }
            quotes[EastmoneyClient.h_secid(row.h_code)] = {
                'price': 8.0 + 0.1 * self.bump, 'volume': 900, 'amount': 9000,
                'pct_change': 1.0, 'change': 0.08, 'prev_close': 7.92,
                'high': 8.2, 'low': 7.9, 'quote_time': now, 'provider': 'tencent_http',
            }
        return quotes, {'price': 0.92, 'quote_time': now, 'provider': 'eastmoney_fx'}


def test_daily_seed_is_explicit_cache_and_stale(tmp_path):
    store = LiveStore(tmp_path / 'live.db')
    LiveMonitor(client=RecordingClient(), store=store)
    latest = store.read_latest()
    assert not latest.empty
    assert set(latest['source']) == {'universe_only'}
    assert latest['a_price'].isna().all()
    assert latest['h_price'].isna().all()
    assert set(latest['quality_state']) == {'等待行情'}
    assert latest['stale_flag'].eq(1).all()


def test_h_only_window_still_fetches_both_stock_legs(tmp_path):
    store = LiveStore(tmp_path / 'live.db')
    client = RecordingClient()
    monitor = LiveMonitor(client=client, store=store)
    state = MarketState('H_ONLY_MORNING', '仅H股交易', False, True, True, 10, 20, 60, 30, premium_mode='indicative_single_leg', a_session='break', h_session='open')
    ids = monitor.pairs['company_id'].head(2).tolist()
    monitor.refresh_queue(QueueDue('priority', 20, ids), state)
    assert client.calls[-1][:3] == (True, True, True)
    rows = store.read_latest().set_index('company_id').loc[ids]
    assert rows['quality_state'].eq('单边指示').all()
    assert rows['a_source'].eq('tencent_http').all()
    assert rows['h_source'].eq('tencent_http').all()
    assert rows['fx_source'].eq('eastmoney_fx').all()
    assert rows['stale_flag'].eq(0).all()


def test_intraday_change_keeps_daily_baseline_between_refreshes(tmp_path):
    store = LiveStore(tmp_path / 'live.db')
    client = RecordingClient()
    monitor = LiveMonitor(client=client, store=store)
    state = MarketState('BOTH_MORNING', 'A/H同时交易', True, True, True, 5, 15, 60, 30)
    cid = monitor.pairs['company_id'].iloc[0]
    # Explicitly inject a trusted completed-day baseline. Bundled demo history is
    # intentionally excluded from v4.4 live change calculations.
    monitor.daily_baseline[cid] = {
        'date': datetime.now(timezone.utc).date().isoformat(),
        'a_close': 9.9, 'h_close': 7.92, 'fx_cnh_per_hkd': 0.92,
        'premium_pct': 9.9 / (7.92 * 0.92) * 100 - 100,
    }
    q = QueueDue('watchlist', 5, [cid])
    monitor.refresh_queue(q, state)
    first = store.latest_map([cid])[cid]['premium_change_pp']
    monitor.refresh_queue(q, state)
    second = store.latest_map([cid])[cid]['premium_change_pp']
    # The second value is still measured from the same completed daily baseline,
    # therefore the H-price change accumulates rather than resetting to only the
    # latest 5-second delta.
    assert first != second
    assert abs(second) > abs(first) or second * first < 0
