from __future__ import annotations

import json
from dataclasses import dataclass

import pandas as pd

from src.config import SETTINGS, UPDATE_LOG_FILE, UNIVERSE_HISTORY_FILE, UNIVERSE_LOG_FILE


@dataclass(frozen=True)
class DataStatus:
    mode: str
    mode_label: str
    registry_count: int
    local_company_count: int
    real_company_count: int
    history_60_plus: int
    failed_count: int
    previous_snapshot_count: int
    universe_source: str
    last_update: str


def _read_json(path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}


def build_data_status(prices: pd.DataFrame, registry_count: int) -> DataStatus:
    update = _read_json(UPDATE_LOG_FILE)
    universe = _read_json(UNIVERSE_LOG_FILE)
    history = _read_json(UNIVERSE_HISTORY_FILE)
    sources = set(prices.get("data_source", pd.Series(dtype=str)).dropna().astype(str).unique())
    if "data_source" in prices.columns:
        source_series = prices["data_source"].fillna("").astype(str)
        real_ids = set(prices.loc[source_series.eq("eastmoney"), "company_id"].unique())
    else:
        real_ids = set()
    if sources and sources == {"eastmoney"}:
        mode, label = "formal", "正式全量数据模式"
    elif real_ids:
        mode, label = "partial", "部分在线更新模式"
    else:
        mode, label = "demo", "全量演示数据模式"
    failed = update.get("failed_pairs") or []
    return DataStatus(
        mode=mode,
        mode_label=label,
        registry_count=int(registry_count),
        local_company_count=int(prices["company_id"].nunique()),
        real_company_count=len(real_ids),
        history_60_plus=int((prices.groupby("company_id").size() >= 60).sum()),
        failed_count=len(failed),
        previous_snapshot_count=int(history.get("previous_validated_snapshot", {}).get("companies", SETTINGS.expected_universe_count)),
        universe_source=str(universe.get("source") or history.get("current_registry", {}).get("source") or "bundled_full_registry"),
        last_update=str(update.get("updated_at") or universe.get("updated_at") or "尚未在线更新"),
    )
