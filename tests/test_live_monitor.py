from datetime import datetime, timezone

from src.data.eastmoney import EastmoneyClient
from src.live_monitor import LiveMonitor
from src.storage.live_store import LiveStore


class FakeClient:
    last_status = {"a_source": "tencent_http", "h_source": "tencent_http", "fx_source": "eastmoney_fx", "errors": {}}

    def fetch_pair_quotes(self, pairs, update_a, update_h, update_fx, batch_size=40):
        now = datetime.now(timezone.utc).isoformat()
        quotes = {}
        for row in pairs.itertuples(index=False):
            quotes[EastmoneyClient.a_secid(row.a_code)] = {
                "price": 10.0, "prev_close": 9.9, "high": 10.1, "low": 9.8,
                "volume": 1000, "amount": 10000, "quote_time": now,
                "provider": "tencent_http",
            }
            quotes[EastmoneyClient.h_secid(row.h_code)] = {
                "price": 8.0, "prev_close": 7.9, "high": 8.1, "low": 7.8,
                "volume": 900, "amount": 9000, "quote_time": now,
                "provider": "tencent_http",
            }
        fx = {"price": 0.92, "quote_time": now, "provider": "eastmoney_fx"}
        return quotes, fx


def test_forced_live_snapshot_covers_full_registry(tmp_path):
    store = LiveStore(tmp_path / "live.db")
    monitor = LiveMonitor(client=FakeClient(), store=store)
    result = monitor.run_once(force=True)
    latest = store.read_latest()
    assert len(latest) >= 100
    assert not result["errors"]
    assert latest["premium_pct"].notna().all()
    assert latest["quality_state"].eq("手动快照").all()
    assert latest["a_source"].eq("tencent_http").all()
    assert latest["h_source"].eq("tencent_http").all()
