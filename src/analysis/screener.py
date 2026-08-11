from __future__ import annotations

import numpy as np
import pandas as pd

from src.analysis.comparability import assess_comparability
from src.analysis.premium import add_premium_features, shapley_contributions
from src.config import SETTINGS


def _severity(change_z: float, percentile: float, residual_pp: float, comp_score: int) -> float:
    z_term = min(abs(change_z) if np.isfinite(change_z) else 0.0, 5.0) / 5.0
    pct_term = abs((percentile if np.isfinite(percentile) else 0.5) - 0.5) * 2
    residual_term = min(abs(residual_pp) / 8.0, 1.0)
    confidence_term = comp_score / 100.0
    return round(100 * (0.40 * z_term + 0.20 * pct_term + 0.25 * residual_term + 0.15 * confidence_term), 1)


def build_screener(prices: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    histories: dict[str, pd.DataFrame] = {}
    latest_rows: list[dict[str, object]] = []

    # First pass: premium features and latest company-level changes.
    for company_id, group in prices.groupby("company_id", sort=False):
        feature_frame = add_premium_features(group, SETTINGS.rolling_window)
        histories[company_id] = feature_frame
        if len(feature_frame) < 2:
            continue
        prev, current = feature_frame.iloc[-2], feature_frame.iloc[-1]
        contribution = shapley_contributions(prev, current)
        comp = assess_comparability(feature_frame)
        latest_rows.append(
            {
                "company_id": company_id,
                "company_name": current.get("company_name", company_id),
                "industry": current.get("industry", "未分类"),
                "date": current["date"],
                "a_ticker": current.get("a_ticker", ""),
                "h_ticker": current.get("h_ticker", ""),
                "a_close": float(current["a_close"]),
                "h_close": float(current["h_close"]),
                "fx_cnh_per_hkd": float(current["fx_cnh_per_hkd"]),
                "a_premium_pct": float(current["a_premium_pct"]),
                "premium_change_pp": float(current["premium_change_pp"]),
                "change_z": float(current["change_z"]) if pd.notna(current["change_z"]) else 0.0,
                "premium_percentile": float(current["premium_percentile"]) if pd.notna(current["premium_percentile"]) else 0.5,
                "comparability_score": comp.score,
                "comparability_label": comp.label,
                "analysis_status": comp.status,
                "comparability_reasons": "；".join(comp.reasons),
                **contribution,
                "data_source": current.get("data_source", "unknown"),
            }
        )

    screen = pd.DataFrame(latest_rows)
    if screen.empty:
        return screen, histories

    industry_median = screen.groupby("industry")["premium_change_pp"].transform("median")
    screen["industry_common_change_pp"] = industry_median
    screen["company_residual_pp"] = screen["premium_change_pp"] - industry_median
    screen["driver_market"] = np.select(
        [
            screen["a_contribution_pp"].abs() >= screen["h_contribution_pp"].abs(),
            screen["h_contribution_pp"].abs() > screen["a_contribution_pp"].abs(),
        ],
        ["A股", "H股"],
        default="汇率",
    )
    # FX can supersede when it is the largest absolute contribution.
    fx_largest = (
        screen["fx_contribution_pp"].abs()
        > screen[["a_contribution_pp", "h_contribution_pp"]].abs().max(axis=1)
    )
    screen.loc[fx_largest, "driver_market"] = "汇率"

    screen["severity_score"] = screen.apply(
        lambda row: _severity(
            row["change_z"],
            row["premium_percentile"],
            row["company_residual_pp"],
            int(row["comparability_score"]),
        ),
        axis=1,
    )
    screen["anomaly_level"] = pd.cut(
        screen["severity_score"],
        bins=[-1, 45, 65, 101],
        labels=["低", "中", "高"],
    ).astype(str)
    screen.loc[screen["analysis_status"] == "排除", "anomaly_level"] = "排除"
    status_rank = {"可分析": 0, "谨慎": 1, "排除": 2}
    screen["_status_rank"] = screen["analysis_status"].map(status_rank).fillna(3)
    screen = screen.sort_values(
        ["_status_rank", "severity_score"],
        ascending=[True, False],
    ).drop(columns="_status_rank").reset_index(drop=True)
    return screen, histories
