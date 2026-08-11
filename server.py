from __future__ import annotations

import asyncio
import io
import json
import math
import sys
from urllib.parse import quote
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analysis.historical import find_similar_events, summarize_analogs
from src.analysis.premium import add_premium_features
from src.config import DATA_DIR, LIVE_DB_FILE, PRICES_FILE, RESULTS_FILE, SETTINGS
from src.data.pairs import load_pairs, sync_universe_from_eastmoney, universe_status
from src.data.history_sync import ensure_company_history, latest_completed_daily_date, market_history_audit, on_demand_sync_enabled
from src.market_clock import get_market_state
from src.reporting.card import build_explanation_card
from src.storage.live_store import LiveStore
from src.storage.focus_state import save_focus
from src.runtime_cache import ServerLiveCache
from src.storage.preferences import load_watchlist, save_watchlist, watchlist_mode
from src.storage.refresh_policy import RefreshPolicy, apply_refresh_policy, load_refresh_policy, save_refresh_policy
from src.live_monitor import LiveMonitor

APP_VERSION = (ROOT / "VERSION.txt").read_text(encoding="utf-8").strip()
WEB_DIR = ROOT / "web"
ASSET_DIR = WEB_DIR / "assets"
INDEX_FILE = WEB_DIR / "index.html"

app = FastAPI(
    title="A/H Cross-Market Research Terminal",
    version=APP_VERSION,
    docs_url="/api/docs",
    redoc_url=None,
)
app.mount("/assets", StaticFiles(directory=ASSET_DIR), name="assets")

LIVE_STORE = LiveStore()
SERVER_LIVE_CACHE = ServerLiveCache(LIVE_STORE, min_check_interval=0.15)


@app.middleware("http")
async def disable_browser_cache(request, call_next):
    response = await call_next(request)
    # This is a local research terminal. Stale cached front-end assets are more
    # harmful than the small bandwidth saving, especially during local upgrades.
    if request.url.path == "/" or request.url.path.startswith("/assets/") or request.url.path == "/index.html":
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


class WatchlistPayload(BaseModel):
    company_ids: list[str]


class ViewPayload(BaseModel):
    name: str
    columns: list[str]
    filters: dict[str, Any] = {}
    sort: dict[str, str] = {}


class RefreshPolicyPayload(BaseModel):
    enabled: bool = False
    watchlist_seconds: int = 5
    priority_seconds: int = 15
    universe_seconds: int = 60
    status_seconds: int = 30


def _mtime(path: Path) -> float:
    return path.stat().st_mtime if path.exists() else 0.0


def _safe(value: Any) -> Any:
    if value is None or (isinstance(value, str) and value.strip() in {"NaT", "nan"}):
        return None
    # pandas.NaT is also an instance of datetime, so test missingness before
    # datetime serialization to avoid leaking the literal string "NaT" to the UI.
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        if value.hour == 0 and value.minute == 0 and value.second == 0 and value.microsecond == 0:
            return value.date().isoformat()
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        if not math.isfinite(float(value)):
            return None
        return float(value)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    return value


def records(df: pd.DataFrame) -> list[dict[str, Any]]:
    return [{str(k): _safe(v) for k, v in row.items()} for row in df.to_dict(orient="records")]


def _runtime_value(entry: dict[str, str] | None) -> Any:
    if not entry:
        return None
    value = entry.get("value")
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def _daily_crawler_status() -> dict[str, Any]:
    path = DATA_DIR / "daily_crawler_status.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"state": "unknown", "error": str(exc)}


@lru_cache(maxsize=4)
def _read_results_cached(mtime: float) -> pd.DataFrame:
    del mtime
    if not RESULTS_FILE.exists():
        return pd.DataFrame()
    df = pd.read_csv(RESULTS_FILE)
    return _decorate_results(df)


@lru_cache(maxsize=4)
def _read_prices_cached(mtime: float) -> pd.DataFrame:
    del mtime
    if not PRICES_FILE.exists():
        return pd.DataFrame()
    df = pd.read_csv(PRICES_FILE)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df.dropna(subset=["date", "company_id"]).sort_values(["company_id", "date"])


@lru_cache(maxsize=4)
def _read_pairs_cached(mtime: float) -> pd.DataFrame:
    del mtime
    return load_pairs()


def read_results() -> pd.DataFrame:
    return _read_results_cached(_mtime(RESULTS_FILE)).copy()


def read_prices() -> pd.DataFrame:
    return _read_prices_cached(_mtime(PRICES_FILE)).copy()


def read_pairs() -> pd.DataFrame:
    pairs_file = DATA_DIR / "ah_pairs.csv"
    return _read_pairs_cached(_mtime(pairs_file)).copy()


def _decorate_results(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    data = df.copy()
    numeric_cols = [
        "a_close", "h_close", "fx_cnh_per_hkd", "a_premium_pct", "premium_change_pp",
        "change_z", "premium_percentile", "comparability_score", "a_contribution_pp",
        "h_contribution_pp", "fx_contribution_pp", "industry_common_change_pp",
        "company_residual_pp", "severity_score",
    ]
    for col in numeric_cols:
        if col in data:
            data[col] = pd.to_numeric(data[col], errors="coerce")

    extreme_premium = data["a_premium_pct"].abs().gt(300)
    extreme_move = data["premium_change_pp"].abs().gt(60)
    low_confidence = data["comparability_score"].fillna(0).lt(70)
    excluded = data["analysis_status"].astype(str).eq("排除")

    data["quality_state"] = "可分析"
    data.loc[low_confidence, "quality_state"] = "谨慎"
    data.loc[excluded | extreme_premium | extreme_move, "quality_state"] = "待核验"

    reasons: list[str] = []
    for _, row in data.iterrows():
        row_reasons: list[str] = []
        if str(row.get("analysis_status", "")) == "排除":
            row_reasons.append(str(row.get("comparability_reasons") or "数据质量规则拦截"))
        if pd.notna(row.get("a_premium_pct")) and abs(float(row["a_premium_pct"])) > 300:
            row_reasons.append("价差超过展示上限，需核验代码映射、价格单位或陈旧报价")
        if pd.notna(row.get("premium_change_pp")) and abs(float(row["premium_change_pp"])) > 60:
            row_reasons.append("单日变化超过展示上限，需核验公司行为或价格时间戳")
        if float(row.get("comparability_score") or 0) < 70:
            row_reasons.append("跨市场可比性评分低于70")
        if not row_reasons:
            row_reasons.append(str(row.get("comparability_reasons") or "通过基础可比性检查"))
        reasons.append("；".join(dict.fromkeys(row_reasons)))
    data["quality_reason"] = reasons

    data["display_premium_pct"] = data["a_premium_pct"].where(data["quality_state"] != "待核验")
    data["display_change_pp"] = data["premium_change_pp"].where(data["quality_state"] != "待核验")
    data["research_state"] = np.select(
        [
            data["quality_state"].eq("待核验"),
            data["severity_score"].ge(75),
            data["severity_score"].ge(55),
        ],
        ["数据核验", "重点关注", "一般观察"],
        default="无需处理",
    )
    data["driver_label"] = data["driver_market"].map({"A股": "A股驱动", "H股": "H股驱动", "汇率": "汇率驱动"}).fillna(data["driver_market"])
    return data



@lru_cache(maxsize=4)
def _live_reference_cached(prices_mtime: float) -> dict[str, dict[str, Any]]:
    del prices_mtime
    prices = read_prices()
    if prices.empty:
        return {}
    # Live statistical references must come from online historical data, not the
    # bundled demo series. Otherwise a real intraday quote could be compared with
    # a synthetic baseline and create a meaningless "today change".
    if "data_source" in prices.columns:
        prices = prices[prices["data_source"].astype(str).str.startswith("eastmoney")].copy()
    if prices.empty:
        return {}
    work = prices[[c for c in ["company_id", "date", "a_close", "h_close", "fx_cnh_per_hkd"] if c in prices.columns]].copy()
    for c in ("a_close", "h_close", "fx_cnh_per_hkd"):
        work[c] = pd.to_numeric(work[c], errors="coerce")
    work = work.dropna(subset=["company_id", "a_close", "h_close", "fx_cnh_per_hkd"])
    work = work[(work["a_close"] > 0) & (work["h_close"] > 0) & (work["fx_cnh_per_hkd"] > 0)]
    work["premium"] = work["a_close"] / (work["h_close"] * work["fx_cnh_per_hkd"]) * 100 - 100
    work["premium_change"] = work.groupby("company_id")["premium"].diff()
    out: dict[str, dict[str, Any]] = {}
    for cid, group in work.groupby("company_id", sort=False):
        tail = group.tail(252)
        changes = tail["premium_change"].dropna()
        premiums = tail["premium"].dropna().to_numpy(dtype=float)
        std = float(changes.tail(60).std(ddof=1)) if len(changes.tail(60)) >= 10 else float("nan")
        out[str(cid)] = {"change_std": std, "premiums": premiums}
    return out


def _severity_live(change_z: float, percentile: float, residual_pp: float, comp_score: float) -> float:
    z_term = min(abs(change_z) if math.isfinite(change_z) else 0.0, 5.0) / 5.0
    pct_term = abs((percentile if math.isfinite(percentile) else 0.5) - 0.5) * 2
    residual_term = min(abs(residual_pp) / 8.0, 1.0)
    confidence_term = max(0.0, min(float(comp_score or 0.0), 100.0)) / 100.0
    return round(100 * (0.40 * z_term + 0.20 * pct_term + 0.25 * residual_term + 0.15 * confidence_term), 1)

def _overlay_live(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or not LIVE_DB_FILE.exists():
        return df
    try:
        live = SERVER_LIVE_CACHE.read_latest()
    except Exception:
        return df
    if live.empty:
        return df

    live = live.copy()
    live["fetched_at"] = pd.to_datetime(live["fetched_at"], errors="coerce", utc=True)
    live["live_age_seconds"] = (pd.Timestamp.now(tz="UTC") - live["fetched_at"]).dt.total_seconds().clip(lower=0)
    # Bundled/daily-cache rows are a continuity fallback only. They must never be
    # overlaid as if they were a successful intraday quote.
    source = live.get("source", pd.Series("", index=live.index)).astype(str)
    quality_live = live.get("quality_state", pd.Series("", index=live.index)).astype(str).eq("实时可比")
    live["online_quote"] = quality_live & ~source.str.contains("daily_cache", regex=False)
    live_cols = [
        "company_id", "a_price", "h_price", "premium_pct", "premium_change_pp",
        "a_contribution_pp", "h_contribution_pp", "fx_contribution_pp", "market_state",
        "source", "fetched_at", "live_age_seconds", "stale_flag", "updated_queue", "online_quote",
    ]
    live = live[[c for c in live_cols if c in live.columns]].rename(
        columns={
            "a_price": "live_a_close", "h_price": "live_h_close", "premium_pct": "live_premium_pct",
            "premium_change_pp": "live_change_pp", "a_contribution_pp": "live_a_contribution_pp",
            "h_contribution_pp": "live_h_contribution_pp", "fx_contribution_pp": "live_fx_contribution_pp",
            "source": "live_source",
        }
    )
    merged = df.merge(live, on="company_id", how="left")
    state = get_market_state()
    max_age = 240 if state.any_open else 3600
    fresh = (
        merged.get("online_quote", pd.Series(False, index=merged.index)).fillna(False).astype(bool)
        & merged["fetched_at"].notna()
        & merged.get("stale_flag", pd.Series(1, index=merged.index)).fillna(1).eq(0)
        & merged.get("live_age_seconds", pd.Series(float("inf"), index=merged.index)).fillna(float("inf")).le(max_age)
    )
    # Current live prices/premium can be overlaid whenever the live gate passes.
    for target, source_col in [("a_close", "live_a_close"), ("h_close", "live_h_close"), ("a_premium_pct", "live_premium_pct")]:
        if source_col in merged:
            mask = fresh & merged[source_col].notna()
            if mask.any():
                merged[target] = pd.to_numeric(merged[target], errors="coerce")
                merged.loc[mask, target] = pd.to_numeric(merged.loc[mask, source_col], errors="coerce").to_numpy()

    # Intraday change/attribution require a real online daily baseline. When the
    # baseline is unavailable, explicitly clear the bundled demo values rather than
    # allowing them to leak into a live research ranking.
    for target, source_col in [("premium_change_pp", "live_change_pp"), ("a_contribution_pp", "live_a_contribution_pp"), ("h_contribution_pp", "live_h_contribution_pp"), ("fx_contribution_pp", "live_fx_contribution_pp")]:
        if source_col in merged and fresh.any():
            merged[target] = pd.to_numeric(merged[target], errors="coerce")
            merged.loc[fresh, target] = pd.to_numeric(merged.loc[fresh, source_col], errors="coerce").to_numpy()

    # Recompute the live research fields so a successful quote changes the research
    # list, not only a hidden quote table.
    refs = _live_reference_cached(_mtime(PRICES_FILE))
    research_ready = fresh & merged.get("live_change_pp", pd.Series(index=merged.index, dtype=float)).notna()
    research_ready &= merged["company_id"].astype(str).isin(set(refs))
    for idx in merged.index[research_ready]:
        cid = str(merged.at[idx, "company_id"])
        ref = refs.get(cid, {})
        change = float(merged.at[idx, "premium_change_pp"])
        premium = float(merged.at[idx, "a_premium_pct"])
        std = ref.get("change_std")
        if std is not None and math.isfinite(float(std)) and float(std) > 1e-9:
            merged.at[idx, "change_z"] = change / float(std)
        premiums = ref.get("premiums")
        if premiums is not None and len(premiums):
            merged.at[idx, "premium_percentile"] = float(np.mean(np.asarray(premiums) <= premium))

    # Clear synthetic research fields for live quotes that do not yet have a real
    # daily baseline. The current price remains visible; research ranking waits.
    baseline_pending = fresh & ~research_ready
    for col in ["change_z", "premium_percentile", "industry_common_change_pp", "company_residual_pp", "severity_score"]:
        if col in merged:
            merged.loc[baseline_pending, col] = np.nan

    if research_ready.any() and "industry" in merged and "premium_change_pp" in merged:
        # Industry common move is computed only from companies whose live move is
        # grounded in a real daily baseline.
        tmp = merged["premium_change_pp"].where(research_ready)
        current_industry = tmp.groupby(merged["industry"]).transform("median")
        merged.loc[research_ready, "industry_common_change_pp"] = current_industry[research_ready]
        merged.loc[research_ready, "company_residual_pp"] = (
            merged.loc[research_ready, "premium_change_pp"] - merged.loc[research_ready, "industry_common_change_pp"]
        )
        for idx in merged.index[research_ready]:
            contribs = {
                "A股": abs(float(merged.at[idx, "a_contribution_pp"] or 0.0)),
                "H股": abs(float(merged.at[idx, "h_contribution_pp"] or 0.0)),
                "汇率": abs(float(merged.at[idx, "fx_contribution_pp"] or 0.0)),
            }
            merged.at[idx, "driver_market"] = max(contribs, key=contribs.get)
            merged.at[idx, "severity_score"] = _severity_live(
                float(merged.at[idx, "change_z"] or 0.0),
                float(merged.at[idx, "premium_percentile"] or 0.5),
                float(merged.at[idx, "company_residual_pp"] or 0.0),
                float(merged.at[idx, "comparability_score"] or 0.0),
            )
    merged["is_live_online"] = fresh
    decorated = _decorate_results(merged)
    if "company_id" in decorated:
        pending_ids = set(merged.loc[baseline_pending, "company_id"].astype(str))
        mask = decorated["company_id"].astype(str).isin(pending_ids)
        decorated.loc[mask, "research_state"] = "基准待更新"
        decorated.loc[mask, "display_change_pp"] = np.nan
    return decorated

def get_results(include_live: bool = True) -> pd.DataFrame:
    df = read_results()
    return _overlay_live(df) if include_live else df


def _default_watchlist_ids(df: pd.DataFrame, limit: int = 5) -> list[str]:
    """Rank the default watchlist by actionable research priority.

    The default list is intentionally *not* a fixed set of company IDs. Eligible
    names must have a completed research baseline, pass the display-quality gate,
    and have comparability >= 80. Ranking uses the existing composite
    ``severity_score``; residual magnitude, |z| and comparability are deterministic
    tie-breakers. If fewer than ``limit`` names meet the strict 80-point gate, the
    function fills from other analyzable baseline-ready names before falling back
    to the available universe.
    """
    if df is None or df.empty or "company_id" not in df:
        return []
    frame = df.copy()
    for col in ["severity_score", "company_residual_pp", "change_z", "comparability_score", "display_change_pp"]:
        if col in frame:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["_baseline_ready"] = ~frame.get(
        "research_state", pd.Series("", index=frame.index, dtype=object)
    ).astype(str).eq("基准待更新")
    frame["_quality_ok"] = frame.get(
        "quality_state", pd.Series("", index=frame.index, dtype=object)
    ).astype(str).eq("可分析")
    frame["_severity_ok"] = frame.get(
        "severity_score", pd.Series(float("nan"), index=frame.index)
    ).notna()
    frame["_residual_abs"] = frame.get(
        "company_residual_pp", pd.Series(0.0, index=frame.index)
    ).abs().fillna(0.0)
    frame["_z_abs"] = frame.get(
        "change_z", pd.Series(0.0, index=frame.index)
    ).abs().fillna(0.0)
    frame["_change_abs"] = frame.get(
        "display_change_pp", pd.Series(0.0, index=frame.index)
    ).abs().fillna(0.0)

    selected: list[str] = []

    def take(pool: pd.DataFrame) -> None:
        nonlocal selected
        if pool.empty or len(selected) >= limit:
            return
        ranked = pool.sort_values(
            ["severity_score", "_residual_abs", "_z_abs", "comparability_score", "_change_abs"],
            ascending=[False, False, False, False, False],
            na_position="last",
        )
        for cid in ranked["company_id"].astype(str):
            if cid not in selected:
                selected.append(cid)
            if len(selected) >= limit:
                break

    strict = frame[
        frame["_baseline_ready"]
        & frame["_quality_ok"]
        & frame["_severity_ok"]
        & frame.get("comparability_score", pd.Series(0.0, index=frame.index)).fillna(0).ge(80)
    ]
    take(strict)
    if len(selected) < limit:
        take(frame[frame["_baseline_ready"] & frame["_quality_ok"] & frame["_severity_ok"]])
    if len(selected) < limit:
        take(frame[frame["_quality_ok"] & frame["_severity_ok"]])
    if len(selected) < limit:
        for cid in frame["company_id"].astype(str):
            if cid not in selected:
                selected.append(cid)
            if len(selected) >= limit:
                break
    return selected[:limit]


def _resolved_watchlist(df: pd.DataFrame, limit: int = 5) -> list[str]:
    ids = df["company_id"].astype(str).tolist() if not df.empty and "company_id" in df else []
    defaults = _default_watchlist_ids(df, limit=limit)
    return load_watchlist(ids, default_count=limit, default_ids=defaults)


def _market_payload() -> dict[str, Any]:
    policy = load_refresh_policy()
    state = apply_refresh_policy(get_market_state(), policy)
    return {
        "code": state.code,
        "label": state.label,
        "a_market_open": state.a_open,
        "h_market_open": state.h_open,
        "any_open": state.any_open,
        "watchlist_seconds": state.watchlist_seconds,
        "priority_seconds": state.priority_seconds,
        "universe_seconds": state.universe_seconds,
        "status_seconds": policy.status_seconds,
        "custom_refresh_enabled": policy.enabled,
        "premium_mode": state.premium_mode,
        "a_session": state.a_session,
        "h_session": state.h_session,
        "a_trading_day": state.a_trading_day,
        "h_trading_day": state.h_trading_day,
    }


def _load_update_log() -> dict[str, Any]:
    path = DATA_DIR / "update_log.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_universe_log() -> dict[str, Any]:
    path = DATA_DIR / "universe_sync_log.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    if not INDEX_FILE.exists():
        raise HTTPException(500, "Front-end file is missing")
    return HTMLResponse(INDEX_FILE.read_text(encoding="utf-8"))


@app.get("/api/health")
def health() -> dict[str, Any]:
    results = get_results(include_live=False)
    return {
        "status": "ok",
        "product": "ah-cross-market-research-terminal",
        "version": APP_VERSION,
        "companies": int(len(read_pairs())),
        "analyzed_companies": int(results["company_id"].nunique()) if not results.empty else 0,
        "target_companies": SETTINGS.expected_universe_count,
        "prices": int(len(read_prices())),
        "market": _market_payload(),
        "runtime_memory": SERVER_LIVE_CACHE.meta(),
        "websocket": True,
        "time": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
    }


@app.get("/api/bootstrap")
def bootstrap() -> dict[str, Any]:
    df = get_results()
    if df.empty:
        raise HTTPException(503, "No analysis results are available")
    ids = df["company_id"].astype(str).tolist()
    watchlist = _resolved_watchlist(df, limit=5)
    valid = df[df["quality_state"].eq("可分析")]
    focus_id = next((cid for cid in watchlist if cid in set(valid["company_id"])), str(valid.iloc[0]["company_id"]) if not valid.empty else str(df.iloc[0]["company_id"]))
    update_log = _load_update_log()
    newest_date = str(df["date"].max()) if "date" in df else None
    live_online = 0
    try:
        live_frame = SERVER_LIVE_CACHE.read_latest()
        if not live_frame.empty and "source" in live_frame:
            live_online = int(live_frame.get("quality_state", pd.Series("", index=live_frame.index)).astype(str).eq("实时可比").sum())
    except Exception:
        live_online = 0
    daily_real = str(update_log.get("status") or "").lower() == "success" and int(update_log.get("real_data_companies") or update_log.get("updated_pairs") or 0) > 0
    data_mode = "online" if daily_real and live_online else "mixed" if live_online else "daily_online" if daily_real else "local_cache"
    return {
        "version": APP_VERSION,
        "market": _market_payload(),
        "watchlist": watchlist,
        "focus_company_id": focus_id,
        "industries": sorted(str(v) for v in df["industry"].dropna().unique()),
        "latest_analysis_date": newest_date,
        "update_log": update_log,
        "data_mode": data_mode,
        "live_online_companies": live_online,
        "universe_log": {**_load_universe_log(), **universe_status()},
        "daily_crawler": _daily_crawler_status(),
    }


@app.get("/api/summary")
def summary() -> dict[str, Any]:
    df = get_results()
    if df.empty:
        return {"total": 0, "analyzable": 0, "high_priority": 0, "quality_issues": 0, "watchlist_alerts": 0}
    watchlist = set(_resolved_watchlist(df, limit=5))
    valid = df["quality_state"].eq("可分析")
    return {
        "total": int(len(read_pairs())),
        "analyzed": int(len(df)),
        "analyzable": int(valid.sum()),
        "high_priority": int((valid & df["severity_score"].ge(75)).sum()),
        "quality_issues": int(df["quality_state"].eq("待核验").sum()),
        "watchlist_alerts": int((df["company_id"].astype(str).isin(watchlist) & valid & df["severity_score"].ge(55)).sum()),
        "median_premium": _safe(df.loc[valid, "a_premium_pct"].median()),
        "median_change": _safe(df.loc[valid, "premium_change_pp"].median()),
        "market": _market_payload(),
        "updated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
    }


@app.get("/api/screener")
def screener(
    search: str = "",
    industry: str = "全部",
    quality: str = "可分析",
    research_state: str = "全部",
    min_change: float = 0.0,
    min_priority: float = 0.0,
    sort_by: str = "severity_score",
    sort_dir: Literal["asc", "desc"] = "desc",
    limit: int = Query(250, ge=1, le=500),
) -> dict[str, Any]:
    df = get_results()
    if df.empty:
        return {"rows": [], "count": 0}
    view = df.copy()
    if search.strip():
        needle = search.strip().lower()
        mask = (
            view["company_name"].astype(str).str.lower().str.contains(needle, regex=False)
            | view["a_ticker"].astype(str).str.lower().str.contains(needle, regex=False)
            | view["h_ticker"].astype(str).str.lower().str.contains(needle, regex=False)
        )
        view = view[mask]
    if industry != "全部":
        view = view[view["industry"].astype(str).eq(industry)]
    if quality != "全部":
        view = view[view["quality_state"].astype(str).eq(quality)]
    if research_state != "全部":
        view = view[view["research_state"].astype(str).eq(research_state)]
    view = view[view["premium_change_pp"].abs().fillna(0).ge(float(min_change))]
    view = view[view["severity_score"].fillna(0).ge(float(min_priority))]
    if sort_by not in view.columns:
        sort_by = "severity_score"
    view = view.sort_values(sort_by, ascending=(sort_dir == "asc"), na_position="last")
    total = int(len(view))
    columns = [
        "company_id", "company_name", "industry", "a_ticker", "h_ticker", "date",
        "a_premium_pct", "premium_change_pp", "display_premium_pct", "display_change_pp",
        "premium_percentile", "change_z", "driver_market", "driver_label",
        "company_residual_pp", "comparability_score", "quality_state", "quality_reason",
        "severity_score", "anomaly_level", "research_state", "a_close", "h_close",
        "a_contribution_pp", "h_contribution_pp", "fx_contribution_pp", "live_age_seconds",
        "market_state", "fetched_at", "updated_queue",
    ]
    columns = [c for c in columns if c in view.columns]
    return {"rows": records(view.head(limit)[columns]), "count": total}


@app.get("/api/companies/search")
def company_search(q: str = "", limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    # Search the entire active A/H universe, including newly added pairs whose
    # historical backfill is still running.
    pairs = read_pairs().copy()
    results = get_results(include_live=False)
    cols_from_results = [c for c in ["company_id", "quality_state", "research_state"] if c in results.columns]
    if cols_from_results:
        pairs = pairs.merge(results[cols_from_results].drop_duplicates("company_id"), on="company_id", how="left")
    pairs["quality_state"] = pairs.get("quality_state", pd.Series(index=pairs.index, dtype=object)).fillna("等待历史数据")
    pairs["research_state"] = pairs.get("research_state", pd.Series(index=pairs.index, dtype=object)).fillna("基准待更新")
    if q.strip():
        needle = q.strip().lower()
        pairs = pairs[
            pairs["company_name"].astype(str).str.lower().str.contains(needle, regex=False)
            | pairs["a_ticker"].astype(str).str.lower().str.contains(needle, regex=False)
            | pairs["h_ticker"].astype(str).str.lower().str.contains(needle, regex=False)
        ]
    cols = ["company_id", "company_name", "a_ticker", "h_ticker", "industry", "quality_state", "research_state"]
    return {"rows": records(pairs.head(limit)[cols])}


def _company_row(company_id: str) -> pd.Series:
    df = get_results()
    match = df[df["company_id"].astype(str).eq(str(company_id))]
    if not match.empty:
        return match.iloc[0]
    pairs = read_pairs()
    pm = pairs[pairs["company_id"].astype(str).eq(str(company_id))]
    if pm.empty:
        raise HTTPException(404, "Company not found")
    base = pm.iloc[0].to_dict()
    live = SERVER_LIVE_CACHE.latest_map([company_id]).get(company_id, {})
    base.update({
        "date": None, "a_close": live.get("a_price"), "h_close": live.get("h_price"),
        "fx_cnh_per_hkd": live.get("fx_cnh_per_hkd"), "a_premium_pct": live.get("premium_pct"),
        "premium_change_pp": live.get("premium_change_pp"), "display_premium_pct": live.get("premium_pct"),
        "display_change_pp": live.get("premium_change_pp"), "premium_percentile": None, "change_z": None,
        "driver_market": "待历史基准", "driver_label": "待历史基准", "company_residual_pp": None,
        "comparability_score": 0, "quality_state": live.get("quality_state") or "等待历史数据",
        "quality_reason": live.get("quality_reason") or "公司已纳入当前A/H公司池，历史日线正在自动补抓",
        "research_state": "基准待更新", "severity_score": 0, "analysis_status": "待历史数据",
        "comparability_reasons": "等待真实历史行情", "a_contribution_pp": None,
        "h_contribution_pp": None, "fx_contribution_pp": None, "industry_common_change_pp": None,
        "fetched_at": live.get("fetched_at"), "live_age_seconds": live.get("data_age_seconds"),
    })
    return pd.Series(base)


@app.get("/api/company/{company_id}")
def company_detail(company_id: str) -> dict[str, Any]:
    row = _company_row(company_id)
    payload = {str(k): _safe(v) for k, v in row.items()}
    baseline_pending = str(row.get("research_state") or "") == "基准待更新"
    payload["baseline_pending"] = baseline_pending

    if baseline_pending:
        # Research statistics do not exist yet. Never render missing attribution as
        # zero, and never publish a reassuring conclusion before the baseline is ready.
        for key in [
            "a_contribution_pp", "h_contribution_pp", "fx_contribution_pp",
            "industry_common_change_pp", "company_residual_pp", "premium_percentile",
            "change_z", "severity_score", "display_change_pp",
        ]:
            payload[key] = None
        live_ok = str(row.get("quality_state") or "") == "可分析"
        headline = "实时行情可比，历史基准正在补齐" if live_ok else "历史基准正在补齐"
        narrative = (
            "当前A股、H股与汇率已通过实时可比性检查；历史分位、价格贡献、行业共同变化、"
            "公司级剩余和研究优先级将在真实日线基准补齐后计算。"
            if live_ok else
            str(row.get("quality_reason") or "公司已纳入当前A/H公司池；真实历史日线正在自动补抓。")
        )
        comp = _safe(row.get("comparability_score"))
        comp_value = float(comp) if comp is not None else 0.0
        payload["checks"] = [
            {"label": "跨市场可比性", "state": "pass" if comp_value >= 80 else "warn", "value": f"{int(comp_value)}/100"},
            {"label": "实时数据状态", "state": "pass" if live_ok else "warn", "value": str(row.get("quality_state") or "等待行情")},
            {"label": "历史基准", "state": "warn", "value": "补抓中"},
            {"label": "研究优先级", "state": "neutral", "value": "待计算"},
        ]
        payload["headline"] = headline
        payload["narrative"] = narrative
        return payload

    residual_raw = _safe(row.get("company_residual_pp"))
    residual = abs(float(residual_raw)) if residual_raw is not None else None
    if row.get("quality_state") == "待核验":
        headline = "当前样本需要先完成数据核验"
        narrative = str(row.get("quality_reason") or "价格、汇率或交易状态未通过展示规则。")
    elif residual is not None and residual >= 2 and float(row.get("comparability_score") or 0) >= 80:
        headline = "存在高置信度公司级剩余异常"
        narrative = (
            f"本次价差变化主要由{row.get('driver_market')}驱动。扣除行业共同变化后，"
            f"仍有 {float(row.get('company_residual_pp')):+.2f} 个百分点无法由同行解释，建议进入研究队列。"
        )
    elif residual is not None and residual >= 1:
        headline = "存在中等强度剩余变化"
        narrative = (
            f"本次主要由{row.get('driver_market')}驱动，公司级剩余为 "
            f"{float(row.get('company_residual_pp')):+.2f} 个百分点，可继续观察新增公告与成交变化。"
        )
    elif residual is not None:
        headline = "变化与行业共同路径基本一致"
        narrative = (
            f"本次主要由{row.get('driver_market')}驱动，公司级剩余仅 "
            f"{float(row.get('company_residual_pp')):+.2f} 个百分点，暂不需要升级调查。"
        )
    else:
        headline = "研究统计暂不可用"
        narrative = "当前数据不足以生成公司级异常结论，请完成数据更新后重试。"

    priority_raw = _safe(row.get("severity_score"))
    priority = float(priority_raw) if priority_raw is not None else None
    comp_raw = _safe(row.get("comparability_score"))
    comp = float(comp_raw) if comp_raw is not None else 0.0
    payload["headline"] = headline
    payload["narrative"] = narrative
    payload["checks"] = [
        {"label": "跨市场可比性", "state": "pass" if comp >= 80 else "warn", "value": f"{int(comp)}/100"},
        {"label": "数据展示规则", "state": "pass" if row.get("quality_state") == "可分析" else "warn", "value": str(row.get("quality_state") or "—")},
        {"label": "公司行为与停牌", "state": "pass" if row.get("analysis_status") == "可分析" else "warn", "value": str(row.get("comparability_reasons") or "—")},
        {"label": "研究优先级", "state": "pass" if priority is not None and priority >= 55 else "neutral", "value": f"{priority:.1f}" if priority is not None else "—"},
    ]
    return payload


@app.get("/api/company/{company_id}/history")
def company_history(company_id: str, days: int = Query(260, ge=5, le=5000)) -> dict[str, Any]:
    # Dashboard/company charts must use the same formal on-demand daily sync as the
    # Market Center. Earlier releases synced only /api/market/.../daily, which meant
    # the research dashboard could remain frozen on the bundled demo snapshot even
    # after 6/7 August formal bars were available online.
    sync: dict[str, Any] = {
        "status": "disabled",
        "expected_through": latest_completed_daily_date().date().isoformat(),
    }
    if on_demand_sync_enabled():
        try:
            sync = ensure_company_history(company_id, days=days, full=False)
            _read_prices_cached.cache_clear()
            _read_results_cached.cache_clear()
        except Exception as exc:
            sync = {
                "status": "failed",
                "error": str(exc),
                "expected_through": latest_completed_daily_date().date().isoformat(),
            }

    prices = read_prices()
    group = prices[prices["company_id"].astype(str).eq(str(company_id))].copy()
    if group.empty:
        return {
            "rows": [], "pending": True, "message": "真实历史行情正在自动补抓",
            "sync": sync, "expected_through": sync.get("expected_through"),
        }

    real_mask = group.get("data_source", pd.Series("", index=group.index)).astype(str).str.startswith("eastmoney")
    if real_mask.any():
        group = group[real_mask].copy()
        data_mode = "online"
    else:
        data_mode = "offline_preview"

    history = add_premium_features(group, SETTINGS.rolling_window).sort_values("date").tail(days).copy()
    history["h_price_cny"] = history["h_close"] * history["fx_cnh_per_hkd"]
    first_a = history["a_close"].dropna()
    first_h = history["h_price_cny"].dropna()
    history["a_normalized"] = history["a_close"] / first_a.iloc[0] * 100 if not first_a.empty else np.nan
    history["h_normalized"] = history["h_price_cny"] / first_h.iloc[0] * 100 if not first_h.empty else np.nan
    cols = [
        "date", "a_close", "h_close", "h_price_cny", "fx_cnh_per_hkd", "a_premium_pct",
        "premium_change_pp", "premium_rolling_median", "change_z", "premium_percentile",
        "a_normalized", "h_normalized", "a_volume", "h_volume", "a_amount", "h_amount",
        "data_source", "fx_source", "ex_dividend_h", "single_side_halt",
    ]
    sources = [str(x) for x in history.get("data_source", pd.Series(dtype=str)).dropna().astype(str).unique().tolist()]
    expected = sync.get("expected_through")
    latest = _safe(history["date"].max()) if not history.empty else None
    return {
        "rows": records(history[[c for c in cols if c in history.columns]]),
        "count": int(len(history)),
        "from": _safe(history["date"].min()) if not history.empty else None,
        "to": latest,
        "sources": sources,
        "data_mode": data_mode,
        "expected_through": expected,
        "is_current": bool(
            pd.notna(pd.to_datetime(latest, errors="coerce"))
            and expected
            and pd.to_datetime(latest, errors="coerce").normalize() >= pd.Timestamp(expected).normalize()
        ) if latest else False,
        "sync": sync,
    }


@app.get("/api/company/{company_id}/analogs")
def company_analogs(company_id: str) -> dict[str, Any]:
    prices = read_prices()
    group = prices[prices["company_id"].astype(str).eq(str(company_id))].copy()
    if group.empty:
        return {"rows": [], "summary": {}, "pending": True, "message": "真实历史行情正在自动补抓"}
    real_mask = group.get("data_source", pd.Series("", index=group.index)).astype(str).str.startswith("eastmoney")
    if real_mask.any():
        group = group[real_mask].copy()
    history = add_premium_features(group, SETTINGS.rolling_window)
    analogs = find_similar_events(history, top_n=8)
    return {"rows": records(analogs), "summary": summarize_analogs(analogs)}


@app.get("/api/company/{company_id}/report")
def company_report(company_id: str) -> StreamingResponse:
    row = _company_row(company_id)
    prices = read_prices()
    group = prices[prices["company_id"].astype(str).eq(str(company_id))].copy()
    real_mask = group.get("data_source", pd.Series("", index=group.index)).astype(str).str.startswith("eastmoney")
    if real_mask.any():
        group = group[real_mask].copy()
    history = add_premium_features(group, SETTINGS.rolling_window)
    card = build_explanation_card(row, history)
    content = card.markdown
    filename = f"{row['company_name']}_AH_dislocation_report.md"
    return StreamingResponse(
        io.BytesIO(content.encode("utf-8-sig")),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@app.get("/api/watchlist")
def get_watchlist() -> dict[str, Any]:
    df = get_results()
    ids = df["company_id"].astype(str).tolist()
    selected = _resolved_watchlist(df, limit=5)
    rows = df[df["company_id"].astype(str).isin(selected)].copy()
    rows["_order"] = rows["company_id"].astype(str).map({cid: i for i, cid in enumerate(selected)})
    rows = rows.sort_values("_order")
    cols = [
        "company_id", "company_name", "a_ticker", "h_ticker", "industry",
        "display_premium_pct", "display_change_pp", "driver_label", "company_residual_pp", "research_state",
        "quality_state", "comparability_score", "severity_score", "live_age_seconds", "fetched_at", "a_close", "h_close",
    ]
    return {
        "company_ids": selected,
        "rows": records(rows[[c for c in cols if c in rows.columns]]),
        "mode": watchlist_mode(),
        "selection_basis": "研究优先级 Top 5（历史基准就绪、可分析、优先可比性≥80）" if watchlist_mode() == "auto_top5" else "用户自定义",
    }


@app.post("/api/watchlist")
def set_watchlist(payload: WatchlistPayload) -> dict[str, Any]:
    available = set(read_pairs()["company_id"].astype(str))
    cleaned = [cid for cid in dict.fromkeys(payload.company_ids) if cid in available]
    save_watchlist(cleaned)
    return {"company_ids": cleaned, "saved": True}


@app.get("/api/data-quality")
def data_quality() -> dict[str, Any]:
    df = get_results()
    issues = df[df["quality_state"].ne("可分析")].copy()
    issues = issues.sort_values(["quality_state", "comparability_score", "severity_score"], ascending=[True, True, False])
    cols = [
        "company_id", "company_name", "a_ticker", "h_ticker", "industry", "a_premium_pct",
        "premium_change_pp", "comparability_score", "quality_state", "quality_reason",
        "analysis_status", "data_source", "fetched_at", "live_age_seconds",
    ]
    summary = {
        "total": int(len(df)),
        "analyzable": int(df["quality_state"].eq("可分析").sum()),
        "caution": int(df["quality_state"].eq("谨慎").sum()),
        "quarantined": int(df["quality_state"].eq("待核验").sum()),
    }
    return {"summary": summary, "rows": records(issues[[c for c in cols if c in issues.columns]])}


@app.get("/api/market/quotes")
def market_quotes(
    q: str = "",
    scope: Literal["all", "watchlist", "priority"] = "all",
    sort_by: Literal["premium", "premium_change", "a_change", "h_change", "company"] = "premium_change",
    direction: Literal["asc", "desc"] = "desc",
    limit: int = Query(500, ge=1, le=1000),
) -> dict[str, Any]:
    store = LIVE_STORE
    live = SERVER_LIVE_CACHE.read_latest()
    pairs = read_pairs()
    if live.empty:
        return {"rows": [], "count": 0, "summary": {"coverage": 0, "fresh": 0}, "market": _market_payload(), "runtime": store.get_status(), "memory": SERVER_LIVE_CACHE.meta()}
    keep = ["company_id", "company_name", "a_code", "h_code", "industry", "a_ticker", "h_ticker"]
    pair_cols = [c for c in keep if c in pairs.columns]
    view = live.merge(pairs[pair_cols].drop_duplicates("company_id"), on="company_id", how="left", suffixes=("", "_pair"))
    for base in ("company_name", "a_code", "h_code", "industry"):
        pair_name = f"{base}_pair"
        if pair_name in view:
            view[base] = view[base].fillna(view[pair_name])
    if "a_ticker" not in view:
        view["a_ticker"] = view["a_code"].astype(str).str.zfill(6)
    if "h_ticker" not in view:
        view["h_ticker"] = view["h_code"].astype(str).str.zfill(5) + ".HK"
    if q.strip():
        needle = q.strip().lower()
        view = view[
            view["company_name"].astype(str).str.lower().str.contains(needle, regex=False)
            | view["a_code"].astype(str).str.lower().str.contains(needle, regex=False)
            | view["h_code"].astype(str).str.lower().str.contains(needle, regex=False)
        ]
    if scope == "watchlist":
        rank_frame = get_results()
        ids = set(load_watchlist(pairs["company_id"].astype(str).tolist(), default_count=5, default_ids=_default_watchlist_ids(rank_frame, 5)))
        view = view[view["company_id"].astype(str).isin(ids)]
    elif scope == "priority":
        priority = get_results(include_live=False).sort_values("severity_score", ascending=False)["company_id"].astype(str).head(30)
        view = view[view["company_id"].astype(str).isin(set(priority))]
    now = pd.Timestamp.now(tz="UTC")
    for col in ("a_quote_time", "h_quote_time", "fx_quote_time", "fetched_at"):
        if col in view:
            view[col] = pd.to_datetime(view[col], errors="coerce", utc=True)
    # Freshness reflects when this terminal fetched the snapshot. a_quote_time /
    # h_quote_time are last-trade timestamps and may legitimately be old for an
    # illiquid security even while the provider response itself is current.
    if "fetched_at" in view:
        view["quote_age_seconds"] = (now - view["fetched_at"]).dt.total_seconds().clip(lower=0)
    else:
        view["quote_age_seconds"] = pd.Series(float("nan"), index=view.index)
    quality = view.get("quality_state", pd.Series("", index=view.index)).astype(str)
    online_mask = quality.eq("实时可比")
    reference_map = {
        "单边指示": "单边",
        "竞价指示": "竞价",
        "午间快照": "午间",
        "上一收盘": "上一收盘",
        "收盘口径": "收盘",
        "单边收盘参考": "收盘参考",
        "手动快照": "手动",
    }
    view["freshness"] = quality.map(reference_map)
    pause_mask = quality.eq("暂停计算")
    cache_mask = quality.isin(["本地缓存", "等待行情", ""])
    view.loc[pause_mask, "freshness"] = "暂停计算"
    view.loc[cache_mask, "freshness"] = "本地缓存"
    realtime_unset = online_mask & view["freshness"].isna()
    view.loc[realtime_unset & view["stale_flag"].fillna(0).astype(int).eq(1), "freshness"] = "陈旧"
    view.loc[realtime_unset & view["quote_age_seconds"].gt(60), "freshness"] = "延迟"
    view.loc[realtime_unset & view["freshness"].isna(), "freshness"] = "实时"
    view["freshness"] = view["freshness"].fillna("本地缓存")
    sort_map = {"premium": "premium_pct", "premium_change": "premium_change_pp", "a_change": "a_pct_change", "h_change": "h_pct_change", "company": "company_name"}
    col = sort_map[sort_by]
    if col in view:
        view = view.sort_values(col, ascending=direction == "asc", na_position="last")
    total = len(view)
    cols = [
        "company_id", "company_name", "a_ticker", "h_ticker", "industry",
        "a_price", "a_pct_change", "a_change", "a_prev_close", "a_open", "a_high", "a_low", "a_volume", "a_amount",
        "h_price", "h_pct_change", "h_change", "h_prev_close", "h_open", "h_high", "h_low", "h_volume", "h_amount",
        "fx_cnh_per_hkd", "premium_pct", "premium_change_pp", "a_contribution_pp", "h_contribution_pp", "fx_contribution_pp",
        "a_quote_time", "h_quote_time", "fx_quote_time", "fetched_at", "quote_age_seconds", "freshness", "market_state", "source", "updated_queue",
        "a_source", "h_source", "fx_source", "quote_skew_seconds", "quality_state", "quality_reason",
        "premium_mode", "a_session", "h_session", "sync_premium_pct", "sync_snapshot_time",
    ]
    runtime = store.get_status()
    fresh = int(view["freshness"].eq("实时").sum())
    indicative = int(view["quality_state"].isin(["单边指示", "竞价指示"]).sum())
    references = int(view["quality_state"].isin(["午间快照", "上一收盘", "收盘口径", "单边收盘参考", "手动快照"]).sum())
    a_src = view.get("a_source", pd.Series("", index=view.index)).astype(str).str.lower()
    h_src = view.get("h_source", pd.Series("", index=view.index)).astype(str).str.lower()
    a_px = pd.to_numeric(view.get("a_price"), errors="coerce")
    h_px = pd.to_numeric(view.get("h_price"), errors="coerce")
    a_online = a_px.gt(0) & ~a_src.str.contains("daily_cache|daily_close|pending|bootstrap", regex=True)
    h_online = h_px.gt(0) & ~h_src.str.contains("daily_cache|daily_close|pending|bootstrap", regex=True)
    summary = {
        "coverage": int(online_mask.sum()), "a_coverage": int(a_online.sum()), "h_coverage": int(h_online.sum()), "visible": int(total), "fresh": fresh,
        "indicative": indicative, "reference": references,
        "a_up": int(pd.to_numeric(view.get("a_pct_change"), errors="coerce").gt(0).sum()) if "a_pct_change" in view else 0,
        "h_up": int(pd.to_numeric(view.get("h_pct_change"), errors="coerce").gt(0).sum()) if "h_pct_change" in view else 0,
        "last_crawl": _safe(view["fetched_at"].max()) if "fetched_at" in view and not view.empty else None,
        "source": ("混合实时行情" if fresh else "指示/收盘参考" if (indicative + references) else "本地缓存/暂停计算"),
    }
    units = {
        "a_volume": "shares", "h_volume": "shares",
        "a_amount": "CNY", "h_amount": "HKD",
        "premium_pct": "A_over_H_percent",
    }
    return {"rows": records(view.head(limit)[[c for c in cols if c in view.columns]]), "count": int(total), "summary": summary, "market": _market_payload(), "runtime": runtime, "daily_crawler": _daily_crawler_status(), "units": units, "memory": SERVER_LIVE_CACHE.meta()}


@app.get("/api/market/quote/{company_id}")
def focused_market_quote(company_id: str) -> dict[str, Any]:
    row = SERVER_LIVE_CACHE.get(company_id)
    if row is None:
        raise HTTPException(404, "Company live quote not found")
    return {
        "company_id": company_id,
        "row": {str(k): _safe(v) for k, v in row.items()},
        "market": _market_payload(),
        "memory": SERVER_LIVE_CACHE.meta(),
    }


@app.post("/api/live/focus/{company_id}")
def set_live_focus(company_id: str) -> dict[str, Any]:
    available = set(read_pairs()["company_id"].astype(str))
    if str(company_id) not in available:
        raise HTTPException(404, "Company not found")
    ids = save_focus(str(company_id))
    return {"saved": True, "company_ids": ids}


@app.websocket("/ws/live/{company_id}")
async def websocket_live_quote(websocket: WebSocket, company_id: str) -> None:
    available = set(read_pairs()["company_id"].astype(str))
    if str(company_id) not in available:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    save_focus(str(company_id))
    last_signature = None
    try:
        while True:
            SERVER_LIVE_CACHE.refresh_if_changed()
            row = SERVER_LIVE_CACHE.get(company_id)
            if row is not None:
                signature = (
                    row.get("fetched_at"), row.get("a_quote_time"), row.get("h_quote_time"),
                    row.get("a_price"), row.get("h_price"), row.get("premium_pct"), row.get("quality_state"),
                )
                if signature != last_signature:
                    last_signature = signature
                    await websocket.send_json({
                        "type": "quote",
                        "company_id": company_id,
                        "row": {str(k): _safe(v) for k, v in row.items()},
                        "market": _market_payload(),
                        "memory": SERVER_LIVE_CACHE.meta(),
                    })
            try:
                # Receiving with a short timeout detects browser disconnects even when
                # the quote itself has not changed. Client messages are optional.
                await asyncio.wait_for(websocket.receive_text(), timeout=0.15)
            except asyncio.TimeoutError:
                pass
    except WebSocketDisconnect:
        return
    except Exception:
        try:
            await websocket.close()
        except Exception:
            pass



def _completed_daily_rows(group: pd.DataFrame, cutoff: pd.Timestamp | None = None) -> tuple[pd.DataFrame, int]:
    """Keep only formally completed common A/H daily observations.

    Some quote providers expose the current intraday K-line with a field named
    ``close`` even before the session has closed.  A daily research chart must not
    interpret that provisional value as an official close.  We therefore apply a
    hard completed-session cutoff first, then require the three core values used by
    every A/H daily chart: A close, H close and HKD/CNY.
    """
    if group.empty:
        return group.copy(), 0

    out = group.copy()
    dates = pd.to_datetime(out.get("date"), errors="coerce")
    completed_through = pd.Timestamp(cutoff if cutoff is not None else latest_completed_daily_date()).normalize()
    keep = dates.notna() & dates.dt.normalize().le(completed_through)

    for column in ("a_close", "h_close", "fx_cnh_per_hkd"):
        if column not in out.columns:
            keep &= False
            continue
        values = pd.to_numeric(out[column], errors="coerce")
        keep &= values.notna() & np.isfinite(values) & values.gt(0)

    excluded = int((~keep).sum())
    out = out.loc[keep].copy()
    if not out.empty:
        out["date"] = dates.loc[keep]
    return out.sort_values("date"), excluded


@app.get("/api/market/daily-status")
def market_daily_status() -> dict[str, Any]:
    """Universe-wide formal daily-history freshness status."""
    return market_history_audit()


@app.get("/api/market/{company_id}/daily")
def market_daily(company_id: str, days: int = Query(260, ge=0, le=5000)) -> dict[str, Any]:
    # days=0 means the complete available common A/H history. This is deliberately
    # different from a large numeric cap such as 5000 trading days.
    full_range = days == 0
    sync: dict[str, Any] = {
        "status": "disabled",
        "expected_through": latest_completed_daily_date().date().isoformat(),
    }
    if on_demand_sync_enabled():
        try:
            sync = ensure_company_history(company_id, days=max(days, 5), full=full_range)
            # The sync may have replaced prices.csv. Drop cached frames explicitly so
            # the current request always renders the newly fetched rows.
            _read_prices_cached.cache_clear()
            _read_results_cached.cache_clear()
        except Exception as exc:
            sync = {
                "status": "failed",
                "error": str(exc),
                "expected_through": latest_completed_daily_date().date().isoformat(),
            }

    prices = read_prices()
    group = prices[prices["company_id"].astype(str).eq(str(company_id))].copy()
    if group.empty:
        return {
            "rows": [], "count": 0, "pending": True,
            "message": "日线历史正在自动补抓", "sync": sync, "frequency": "1D",
        }

    # Once formal Eastmoney history exists for a company, never splice bundled demo
    # rows into the same chart. The demo remains only as an offline fallback.
    real_mask = group.get("data_source", pd.Series("", index=group.index)).astype(str).str.startswith("eastmoney")
    if real_mask.any():
        group = group[real_mask].copy()
        data_mode = "online"
    else:
        data_mode = "offline_preview"

    # Daily charts show completed daily observations only.  During the trading day
    # today's provider K-line can contain a provisional value in the field named
    # `close`; it is still an intraday price and must not appear as today's close.
    # Filtering here is the final API guard even if an older crawler run accidentally
    # persisted such a partial row to prices.csv.
    completed_through = latest_completed_daily_date().normalize()
    group, excluded_incomplete_rows = _completed_daily_rows(group, completed_through)

    history = add_premium_features(group, SETTINGS.rolling_window).sort_values("date").copy()
    if not full_range:
        history = history.tail(days).copy()
    history["h_price_cny"] = history["h_close"] * history["fx_cnh_per_hkd"]
    if not history.empty:
        first_a = history["a_close"].dropna()
        first_h = history["h_price_cny"].dropna()
        history["a_normalized"] = history["a_close"] / first_a.iloc[0] * 100 if not first_a.empty else np.nan
        history["h_normalized"] = history["h_price_cny"] / first_h.iloc[0] * 100 if not first_h.empty else np.nan
        history["a_daily_pct"] = history["a_close"].pct_change() * 100
        history["h_daily_pct"] = history["h_close"].pct_change() * 100
    cols = [
        "date", "a_close", "h_close", "h_price_cny", "fx_cnh_per_hkd", "a_premium_pct",
        "premium_change_pp", "a_normalized", "h_normalized", "a_daily_pct", "h_daily_pct",
        "a_volume", "h_volume", "a_amount", "h_amount", "data_source",
    ]
    sources = []
    if "data_source" in history.columns:
        sources = [str(x) for x in history["data_source"].dropna().astype(str).unique().tolist()]
    return {
        "rows": records(history[[c for c in cols if c in history.columns]]),
        "count": int(len(history)),
        "from": _safe(history["date"].min()) if not history.empty else None,
        "to": _safe(history["date"].max()) if not history.empty else None,
        "sources": sources,
        "frequency": "1D",
        "range": "all" if full_range else "window",
        "requested_days": None if full_range else days,
        "data_mode": data_mode,
        "completed_only": True,
        "completed_through": completed_through.date().isoformat(),
        "excluded_incomplete_rows": excluded_incomplete_rows,
        "expected_through": sync.get("expected_through"),
        "sync": sync,
        "history_start_basis": "first_common_ah_trading_day" if full_range and data_mode == "online" else None,
    }


@app.get("/api/market/{company_id}/intraday")
def market_intraday(company_id: str, limit: int = Query(600, ge=20, le=3000)) -> dict[str, Any]:
    store = LIVE_STORE
    rows = store.read_snapshots(company_id, limit=limit)
    latest = SERVER_LIVE_CACHE.latest_map([company_id]).get(company_id)
    return {"rows": records(rows), "latest": {k: _safe(v) for k, v in (latest or {}).items()}}


def _manual_market_crawl() -> None:
    store = LiveStore()
    store.set_status("manual_crawl", {"state": "running", "started_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()})
    try:
        result = LiveMonitor(store=store).run_once(force=True)
        store.set_status("manual_crawl", {"state": "success", "finished_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(), "result": result})
    except Exception as exc:
        store.set_status("manual_crawl", {"state": "failed", "finished_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(), "error": str(exc)})


@app.post("/api/market/crawl-now")
def market_crawl_now(background_tasks: BackgroundTasks) -> dict[str, Any]:
    background_tasks.add_task(_manual_market_crawl)
    return {"started": True, "message": "已启动混合行情源全量快照抓取", "time": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()}


@app.get("/api/universe/status")
def api_universe_status() -> dict[str, Any]:
    status = universe_status()
    pairs = read_pairs()
    return {**status, "unique_a_codes": int(pairs["a_code"].nunique()), "unique_h_codes": int(pairs["h_code"].nunique())}


@app.post("/api/universe/sync")
def api_universe_sync() -> dict[str, Any]:
    result = sync_universe_from_eastmoney()
    _read_pairs_cached.cache_clear()
    return {"result": result, "status": universe_status()}


@app.get("/api/live/refresh-policy")
def get_refresh_policy_api() -> dict[str, Any]:
    policy = load_refresh_policy()
    return {
        "enabled": policy.enabled,
        "watchlist_seconds": policy.watchlist_seconds,
        "priority_seconds": policy.priority_seconds,
        "universe_seconds": policy.universe_seconds,
        "status_seconds": policy.status_seconds,
    }


@app.post("/api/live/refresh-policy")
def set_refresh_policy_api(payload: RefreshPolicyPayload) -> dict[str, Any]:
    policy = RefreshPolicy(
        enabled=bool(payload.enabled),
        watchlist_seconds=max(1, min(300, int(payload.watchlist_seconds))),
        priority_seconds=max(1, min(600, int(payload.priority_seconds))),
        universe_seconds=max(5, min(1800, int(payload.universe_seconds))),
        status_seconds=max(1, min(300, int(payload.status_seconds))),
    )
    save_refresh_policy(policy)
    return {
        "saved": True,
        "policy": {
            "enabled": policy.enabled,
            "watchlist_seconds": policy.watchlist_seconds,
            "priority_seconds": policy.priority_seconds,
            "universe_seconds": policy.universe_seconds,
            "status_seconds": policy.status_seconds,
        },
        "market": _market_payload(),
    }


@app.get("/api/live/status")
def live_status() -> dict[str, Any]:
    state = _market_payload()
    try:
        store = LIVE_STORE
        latest = SERVER_LIVE_CACHE.read_latest()
        runtime = store.get_status()
    except Exception as exc:
        return {"market": state, "runtime": {}, "coverage": 0, "fresh": 0, "error": str(exc)}
    fresh = 0
    online = 0
    indicative = 0
    reference = 0
    if not latest.empty:
        latest["fetched_at"] = pd.to_datetime(latest["fetched_at"], errors="coerce", utc=True)
        age = (pd.Timestamp.now(tz="UTC") - latest["fetched_at"]).dt.total_seconds()
        quality = latest.get("quality_state", pd.Series("", index=latest.index)).astype(str)
        online_mask = quality.eq("实时可比")
        online = int(online_mask.sum())
        indicative = int(quality.isin(["单边指示", "竞价指示"]).sum())
        reference = int(quality.isin(["午间快照", "上一收盘", "收盘口径", "单边收盘参考", "手动快照"]).sum())
        fresh = int((online_mask & (latest["stale_flag"].fillna(1).astype(int) == 0) & age.lt(240)).sum())
    update_log = _load_update_log()
    daily_real = str(update_log.get("status") or "").lower() == "success" and int(update_log.get("real_data_companies") or update_log.get("updated_pairs") or 0) > 0
    data_mode = "online" if daily_real and online else "mixed" if online else "daily_online" if daily_real else "local_cache"
    return {"market": state, "runtime": runtime, "coverage": int(len(latest)), "online": online, "fresh": fresh, "indicative": indicative, "reference": reference, "data_mode": data_mode, "crawler": _runtime_value(runtime.get("crawler")), "providers": _runtime_value(runtime.get("providers")), "memory": SERVER_LIVE_CACHE.meta(), "websocket": {"enabled": True, "path": "/ws/live/{company_id}"}, "daily_crawler": _daily_crawler_status(), "update_log": update_log}


@app.get("/api/export/screener.csv")
def export_screener() -> StreamingResponse:
    df = get_results()
    output = io.StringIO()
    df.to_csv(output, index=False)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=AH_screener.csv"},
    )


@app.get("/{path:path}")
def spa_fallback(path: str) -> FileResponse:
    candidate = WEB_DIR / path
    if candidate.exists() and candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(INDEX_FILE)
