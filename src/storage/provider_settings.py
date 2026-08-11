from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from src.config import PROVIDER_SETTINGS_FILE


@dataclass(frozen=True)
class ProviderSettings:
    fx_cache_seconds: int = 30
    open_leg_max_age_seconds: int = 20
    both_market_max_skew_seconds: int = 20
    fx_max_age_seconds: int = 180
    request_timeout_seconds: float = 4.0
    circuit_failure_threshold: int = 3
    circuit_cooldown_seconds: int = 15
    ewma_alpha: float = 0.25
    fast_retry_attempts: int = 1
    retry_jitter_min_ms: int = 40
    retry_jitter_max_ms: int = 140


def _bounded(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _bounded_float(value: object, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def load_provider_settings(path: Path = PROVIDER_SETTINGS_FILE) -> ProviderSettings:
    if not path.exists():
        return ProviderSettings()
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return ProviderSettings()
    jitter_min = _bounded(raw.get('retry_jitter_min_ms'), 40, 0, 2000)
    jitter_max = _bounded(raw.get('retry_jitter_max_ms'), 140, jitter_min, 5000)
    return ProviderSettings(
        fx_cache_seconds=_bounded(raw.get('fx_cache_seconds'), 30, 1, 3600),
        open_leg_max_age_seconds=_bounded(raw.get('open_leg_max_age_seconds'), 20, 1, 600),
        both_market_max_skew_seconds=_bounded(raw.get('both_market_max_skew_seconds'), 20, 1, 600),
        fx_max_age_seconds=_bounded(raw.get('fx_max_age_seconds'), 180, 1, 7200),
        request_timeout_seconds=_bounded_float(raw.get('request_timeout_seconds'), 4.0, 0.5, 30.0),
        circuit_failure_threshold=_bounded(raw.get('circuit_failure_threshold'), 3, 1, 20),
        circuit_cooldown_seconds=_bounded(raw.get('circuit_cooldown_seconds'), 15, 1, 300),
        ewma_alpha=_bounded_float(raw.get('ewma_alpha'), 0.25, 0.05, 1.0),
        fast_retry_attempts=_bounded(raw.get('fast_retry_attempts'), 1, 0, 3),
        retry_jitter_min_ms=jitter_min,
        retry_jitter_max_ms=jitter_max,
    )
