from __future__ import annotations

import pandas as pd


def build_alerts(
    screen: pd.DataFrame,
    watchlist: list[str] | None = None,
    severity_threshold: float = 65.0,
    absolute_change_threshold: float = 2.0,
    watchlist_only: bool = False,
) -> pd.DataFrame:
    """Create an auditable alert queue from the latest screener snapshot."""
    if screen.empty:
        return pd.DataFrame()

    data = screen.copy()
    watchset = set(watchlist or [])
    data["is_watchlist"] = data["company_id"].isin(watchset)
    if watchlist_only and watchset:
        data = data[data["is_watchlist"]]

    rows: list[dict[str, object]] = []
    for _, row in data.iterrows():
        triggers: list[str] = []
        if float(row["severity_score"]) >= severity_threshold:
            triggers.append(f"严重度 {row['severity_score']:.0f}")
        if abs(float(row["premium_change_pp"])) >= absolute_change_threshold:
            triggers.append(f"单日变化 {row['premium_change_pp']:+.2f}pp")
        if row["analysis_status"] != "可分析":
            triggers.append(str(row["comparability_reasons"]))
        if not triggers:
            continue

        if row["analysis_status"] == "排除":
            urgency = "数据拦截"
            action = "先核查停牌、除息或陈旧价格"
        elif float(row["severity_score"]) >= 75 and row["analysis_status"] == "可分析":
            urgency = "高"
            action = "进入异常调查并生成解释卡"
        elif float(row["severity_score"]) >= 60:
            urgency = "中"
            action = "加入观察池，等待新增证据"
        else:
            urgency = "低"
            action = "记录事件，无需立即处理"

        rows.append(
            {
                "urgency": urgency,
                "company_id": row["company_id"],
                "company_name": row["company_name"],
                "industry": row["industry"],
                "is_watchlist": bool(row["is_watchlist"]),
                "trigger": "；".join(triggers),
                "driver_market": row["driver_market"],
                "comparability_score": int(row["comparability_score"]),
                "recommended_action": action,
                "severity_score": float(row["severity_score"]),
            }
        )

    result = pd.DataFrame(rows)
    if result.empty:
        return result
    rank = {"高": 0, "数据拦截": 1, "中": 2, "低": 3}
    result["_rank"] = result["urgency"].map(rank).fillna(9)
    return result.sort_values(["_rank", "severity_score"], ascending=[True, False]).drop(columns="_rank").reset_index(drop=True)
