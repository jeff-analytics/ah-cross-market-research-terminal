from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.config import SETTINGS


@dataclass(frozen=True)
class ComparabilityResult:
    score: int
    label: str
    status: str
    reasons: tuple[str, ...]


def assess_comparability(frame: pd.DataFrame) -> ComparabilityResult:
    data = frame.sort_values("date").tail(max(SETTINGS.stale_sessions + 2, 20)).copy()
    if len(data) < 5:
        return ComparabilityResult(20, "数据不足", "排除", ("有效共同交易日不足",))

    score = 100
    reasons: list[str] = []
    latest = data.iloc[-1]

    required = ["a_close", "h_close", "fx_cnh_per_hkd"]
    if latest[required].isna().any() or (latest[required] <= 0).any():
        score -= 70
        reasons.append("最新价格或汇率缺失")

    if int(latest.get("single_side_halt", 0) or 0) == 1:
        score -= 65
        reasons.append("检测到单边停牌或陈旧H股价格")

    if int(latest.get("ex_dividend_h", 0) or 0) == 1:
        score -= 30
        reasons.append("H股处于除息影响窗口")

    stale_n = SETTINGS.stale_sessions
    a_flat = data["a_close"].tail(stale_n).nunique(dropna=True) <= 1
    h_flat = data["h_close"].tail(stale_n).nunique(dropna=True) <= 1
    a_moved = data["a_close"].tail(stale_n + 1).pct_change().abs().sum() > 0.005
    h_moved = data["h_close"].tail(stale_n + 1).pct_change().abs().sum() > 0.005
    if h_flat and a_moved:
        score -= 50
        reasons.append("H股连续多日无价格变化，而A股仍在波动")
    if a_flat and h_moved:
        score -= 50
        reasons.append("A股连续多日无价格变化，而H股仍在波动")

    a_turnover = (data["a_close"] * data["a_volume"]).tail(20).median()
    h_turnover = (data["h_close"] * data["h_volume"]).tail(20).median()
    if pd.notna(a_turnover) and a_turnover < SETTINGS.low_liquidity_a_cny:
        score -= 12
        reasons.append("A股近20日成交金额偏低")
    if pd.notna(h_turnover) and h_turnover < SETTINGS.low_liquidity_h_hkd:
        score -= 18
        reasons.append("H股近20日成交金额偏低")

    if float(latest.get("a_volume", 0) or 0) <= 0 or float(latest.get("h_volume", 0) or 0) <= 0:
        score -= 25
        reasons.append("最新交易日一侧成交量为零")

    score = max(0, min(100, int(round(score))))
    if score >= 80:
        label, status = "高", "可分析"
    elif score >= 60:
        label, status = "中", "谨慎"
    else:
        label, status = "低", "排除"
    if not reasons:
        reasons.append("未发现明显机械价格问题")
    return ComparabilityResult(score, label, status, tuple(dict.fromkeys(reasons)))
