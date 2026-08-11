from __future__ import annotations

import pandas as pd

from src.market_clock import get_market_state
from src.storage.live_store import LiveStore


def load_live_snapshot() -> tuple[pd.DataFrame, dict, object]:
    store = LiveStore()
    latest = store.read_latest()
    status = store.get_status()
    state = get_market_state()
    if not latest.empty:
        latest["fetched_at"] = pd.to_datetime(latest["fetched_at"], errors="coerce", utc=True)
        latest["age_now_seconds"] = (pd.Timestamp.now(tz="UTC") - latest["fetched_at"]).dt.total_seconds().clip(lower=0)
        intervals = {
            "watchlist": state.watchlist_seconds,
            "priority": state.priority_seconds,
            "universe": state.universe_seconds,
            "daily_seed": state.universe_seconds,
        }
        expected = latest["updated_queue"].map(intervals).fillna(state.universe_seconds or 60).clip(lower=5)
        remote_stale = latest["stale_flag"].fillna(0).astype(int).eq(1)
        heartbeat_stale = state.any_open & latest["age_now_seconds"].gt((expected * 2.5).clip(lower=30))
        latest["stale_now"] = (remote_stale | heartbeat_stale).astype(int)
    return latest, status, state
