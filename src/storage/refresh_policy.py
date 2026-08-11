from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from src.config import REFRESH_POLICY_FILE
from src.market_clock import MarketState


@dataclass(frozen=True)
class RefreshPolicy:
    enabled: bool = False
    watchlist_seconds: int = 5
    priority_seconds: int = 15
    universe_seconds: int = 60
    status_seconds: int = 30


def _bounded(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def load_refresh_policy(path: Path = REFRESH_POLICY_FILE) -> RefreshPolicy:
    if not path.exists():
        return RefreshPolicy()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return RefreshPolicy()
    return RefreshPolicy(
        enabled=bool(raw.get("enabled", False)),
        watchlist_seconds=_bounded(raw.get("watchlist_seconds"), 5, 1, 300),
        priority_seconds=_bounded(raw.get("priority_seconds"), 15, 1, 600),
        universe_seconds=_bounded(raw.get("universe_seconds"), 60, 5, 1800),
        status_seconds=_bounded(raw.get("status_seconds"), 15, 1, 300),
    )


def save_refresh_policy(policy: RefreshPolicy, path: Path = REFRESH_POLICY_FILE) -> RefreshPolicy:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(policy), ensure_ascii=False, indent=2), encoding="utf-8")
    return policy


def apply_refresh_policy(state: MarketState, policy: RefreshPolicy | None = None) -> MarketState:
    policy = policy or load_refresh_policy()
    if not policy.enabled or not state.any_open:
        return state
    return MarketState(
        code=state.code,
        label=state.label,
        a_open=state.a_open,
        h_open=state.h_open,
        fx_open=state.fx_open,
        watchlist_seconds=policy.watchlist_seconds,
        priority_seconds=policy.priority_seconds,
        universe_seconds=policy.universe_seconds,
        store_seconds=state.store_seconds,
        premium_mode=state.premium_mode,
        a_session=state.a_session,
        h_session=state.h_session,
        a_trading_day=state.a_trading_day,
        h_trading_day=state.h_trading_day,
    )
