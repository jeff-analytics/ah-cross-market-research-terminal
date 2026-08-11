from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
SOURCE_DIR = DATA_DIR / "source"
PAIRS_FILE = DATA_DIR / "ah_pairs.csv"
BOOTSTRAP_PAIRS_FILE = DATA_DIR / "bootstrap_ah_pairs.csv"
PRICES_FILE = DATA_DIR / "prices.csv"
RESULTS_FILE = DATA_DIR / "latest_results.csv"
UPDATE_LOG_FILE = DATA_DIR / "update_log.json"
UNIVERSE_LOG_FILE = DATA_DIR / "universe_sync_log.json"
UNIVERSE_HISTORY_FILE = DATA_DIR / "universe_history.json"
UNIVERSE_SNAPSHOT_FILE = DATA_DIR / "universe_snapshot_202.json"
LIVE_DB_FILE = DATA_DIR / "live_monitor.db"
LIVE_PID_FILE = DATA_DIR / "live_monitor.pid"
LIVE_STOP_FILE = DATA_DIR / "live_monitor.stop"
MARKET_CALENDAR_FILE = DATA_DIR / "market_calendar_overrides.csv"
REFRESH_POLICY_FILE = DATA_DIR / "refresh_policy.json"
PROVIDER_SETTINGS_FILE = DATA_DIR / "provider_settings.json"
FOCUS_STATE_FILE = DATA_DIR / "focus_state.json"
DEMO_SNAPSHOT_FILE = DATA_DIR / "demo_snapshot.json"
HISTORY_COVERAGE_FILE = DATA_DIR / "history_coverage.json"


def _env_int(name: str, default: int, minimum: int | None = None) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value) if minimum is not None else value


def _bootstrap_active_count() -> int:
    """Derive the validated baseline from the bundled registry, not a magic number."""
    if not BOOTSTRAP_PAIRS_FILE.exists():
        return 0
    try:
        with BOOTSTRAP_PAIRS_FILE.open('r', encoding='utf-8-sig', newline='') as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            return 0
        if 'status' not in rows[0]:
            return len(rows)
        return sum(str(row.get('status') or 'active').strip().lower() == 'active' for row in rows)
    except Exception:
        return 0


_VALIDATED_BASELINE_COUNT = _bootstrap_active_count()


@dataclass(frozen=True)
class Settings:
    rolling_window: int = 60
    percentile_window: int = 252
    anomaly_z_threshold: float = 1.8
    low_liquidity_a_cny: float = 20_000_000.0
    low_liquidity_h_hkd: float = 10_000_000.0
    stale_sessions: int = 3
    default_fx_cnh_per_hkd: float = 0.92
    # These values are derived/configurable rather than tied to a specific release date.
    expected_universe_count: int = _VALIDATED_BASELINE_COUNT
    minimum_valid_universe: int = max(1, int(round(_VALIDATED_BASELINE_COUNT * 0.94))) if _VALIDATED_BASELINE_COUNT else 1
    bootstrap_demo_days: int = _env_int('AH_DEMO_TRADING_DAYS', 320, 20)
    daily_history_years: int = _env_int('AH_DAILY_HISTORY_YEARS', 4, 1)
    on_demand_history_sync: int = _env_int('AH_ON_DEMAND_HISTORY_SYNC', 1, 0)
    universe_sync_hours: int = _env_int('AH_UNIVERSE_SYNC_HOURS', 6, 1)


SETTINGS = Settings()
