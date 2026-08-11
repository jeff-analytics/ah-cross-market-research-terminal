from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from src.config import HISTORY_COVERAGE_FILE, SETTINGS
from src.data.loader import load_prices, update_from_eastmoney
from src.data.pairs import load_pairs

TZ = ZoneInfo("Asia/Shanghai")
FULL_HISTORY_BEGIN = "0"  # Eastmoney convention: request all available history.


def latest_completed_daily_date(now: datetime | None = None) -> pd.Timestamp:
    """Return the latest date whose daily bar should be complete for A/H research.

    During a trading day, today's close is intentionally excluded. After both A and
    H markets have had time to close, today becomes eligible. Weekends are skipped.
    Exchange holidays are harmless: Eastmoney simply returns the latest actual bar.
    """
    current = now.astimezone(TZ) if now is not None and now.tzinfo else (now.replace(tzinfo=TZ) if now is not None else datetime.now(TZ))
    candidate = current.date() if current.time() >= time(16, 20) else current.date() - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return pd.Timestamp(candidate)


def _calendar_start_for_window(target: pd.Timestamp, days: int) -> str:
    # Trading-day windows need a calendar-day buffer for weekends and holidays.
    # 1.75x is deliberately generous and still keeps the request compact.
    calendar_days = max(45, int(days * 1.75) + 20)
    return (target - pd.Timedelta(days=calendar_days)).strftime("%Y%m%d")


def _load_coverage() -> dict:
    if not HISTORY_COVERAGE_FILE.exists():
        return {"companies": {}}
    try:
        payload = json.loads(HISTORY_COVERAGE_FILE.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {"companies": {}}
        payload.setdefault("companies", {})
        return payload
    except Exception:
        return {"companies": {}}


def _save_coverage(payload: dict) -> None:
    HISTORY_COVERAGE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _real_company_rows(company_id: str) -> pd.DataFrame:
    prices = load_prices()
    company = prices[prices["company_id"].astype(str).eq(str(company_id))].copy()
    if "data_source" in company.columns:
        company = company[company["data_source"].astype(str).str.startswith("eastmoney")].copy()
    return company.sort_values("date")


def ensure_company_history(company_id: str, days: int = 260, full: bool = False) -> dict:
    """Synchronize the selected company's daily history on demand.

    - A normal window fetches enough real history for that window and refreshes it
      through the latest completed daily session.
    - ``full=True`` requests Eastmoney's complete available A/H history. Because the
      A and H legs are inner-joined, the returned series starts on the first date on
      which both listings have comparable daily bars.
    """
    target = latest_completed_daily_date()
    target_str = target.strftime("%Y%m%d")
    pairs = load_pairs(active_only=True)
    row = pairs[pairs["company_id"].astype(str).eq(str(company_id))]
    if row.empty:
        return {"status": "unknown_company", "company_id": company_id, "expected_through": target.date().isoformat()}

    real = _real_company_rows(company_id)
    latest = pd.to_datetime(real["date"], errors="coerce").max() if not real.empty else pd.NaT
    earliest = pd.to_datetime(real["date"], errors="coerce").min() if not real.empty else pd.NaT
    coverage = _load_coverage()
    marker = coverage.get("companies", {}).get(str(company_id), {})
    marker_full = bool(marker.get("full_history"))

    if full:
        if marker_full and pd.notna(latest) and latest.normalize() >= target.normalize():
            return {
                "status": "current",
                "company_id": company_id,
                "full_history": True,
                "expected_through": target.date().isoformat(),
                "earliest": earliest.date().isoformat() if pd.notna(earliest) else None,
                "latest": latest.date().isoformat() if pd.notna(latest) else None,
            }
        if marker_full and pd.notna(latest):
            # Full history is already present; only append the missing recent bars.
            result = update_from_eastmoney(end=target_str, company_ids=[company_id], throttle_seconds=0.0)
        else:
            result = update_from_eastmoney(
                start=FULL_HISTORY_BEGIN,
                end=target_str,
                company_ids=[company_id],
                throttle_seconds=0.0,
                force_start=True,
            )
    else:
        desired_days = max(5, int(days))
        have_enough = len(real) >= desired_days
        is_current = pd.notna(latest) and latest.normalize() >= target.normalize()
        if have_enough and is_current:
            return {
                "status": "current",
                "company_id": company_id,
                "full_history": marker_full,
                "expected_through": target.date().isoformat(),
                "earliest": earliest.date().isoformat() if pd.notna(earliest) else None,
                "latest": latest.date().isoformat() if pd.notna(latest) else None,
            }
        if have_enough and pd.notna(latest):
            result = update_from_eastmoney(end=target_str, company_ids=[company_id], throttle_seconds=0.0)
        else:
            start = _calendar_start_for_window(target, desired_days)
            result = update_from_eastmoney(
                start=start,
                end=target_str,
                company_ids=[company_id],
                throttle_seconds=0.0,
                force_start=True,
            )

    refreshed = _real_company_rows(company_id)
    new_latest = pd.to_datetime(refreshed["date"], errors="coerce").max() if not refreshed.empty else pd.NaT
    new_earliest = pd.to_datetime(refreshed["date"], errors="coerce").min() if not refreshed.empty else pd.NaT
    fetched = bool(result.get("updated_pairs", 0))
    current_through_target = bool(pd.notna(new_latest) and new_latest.normalize() >= target.normalize())
    success = fetched and current_through_target

    if fetched:
        companies = coverage.setdefault("companies", {})
        prior = companies.get(str(company_id), {})
        companies[str(company_id)] = {
            **prior,
            # Only certify full history when the series also reaches the latest
            # completed common-session target. A stale partial fetch is retained
            # locally but is never marked as a completed backfill.
            "full_history": bool((full or prior.get("full_history")) and current_through_target),
            "earliest": new_earliest.date().isoformat() if pd.notna(new_earliest) else None,
            "latest": new_latest.date().isoformat() if pd.notna(new_latest) else None,
            "expected_through": target.date().isoformat(),
            "updated_at": datetime.now(TZ).isoformat(),
            "source": "eastmoney_ah+eastmoney_or_ecb_fx",
        }
        _save_coverage(coverage)

    status = "updated" if success else ("stale" if fetched else "failed")
    lag_days = (target.normalize() - new_latest.normalize()).days if pd.notna(new_latest) else None
    return {
        "status": status,
        "company_id": company_id,
        "full_history": bool((full or marker_full) and current_through_target),
        "expected_through": target.date().isoformat(),
        "earliest": new_earliest.date().isoformat() if pd.notna(new_earliest) else None,
        "latest": new_latest.date().isoformat() if pd.notna(new_latest) else None,
        "lag_calendar_days": lag_days,
        "result": result,
    }



def market_history_audit() -> dict:
    """Audit formal daily-history freshness for the *entire active A/H universe*.

    This is intentionally universe-wide.  It prevents the UI from treating an
    on-demand refresh of one selected company as proof that market history is
    current for every company.
    """
    target = latest_completed_daily_date().normalize()
    pairs = load_pairs(active_only=True).copy()
    prices = load_prices()
    if "data_source" in prices.columns:
        real = prices[prices["data_source"].astype(str).str.startswith("eastmoney")].copy()
    else:
        real = prices.copy()
    real["date"] = pd.to_datetime(real.get("date"), errors="coerce")
    latest_map = real.groupby(real["company_id"].astype(str))["date"].max().to_dict() if not real.empty else {}

    rows = []
    current_ids = []
    stale_ids = []
    for row in pairs.itertuples(index=False):
        cid = str(row.company_id)
        latest = latest_map.get(cid, pd.NaT)
        is_current = bool(pd.notna(latest) and pd.Timestamp(latest).normalize() >= target)
        if is_current:
            current_ids.append(cid)
        else:
            stale_ids.append(cid)
        rows.append({
            "company_id": cid,
            "company_name": str(row.company_name),
            "latest_formal_date": pd.Timestamp(latest).date().isoformat() if pd.notna(latest) else None,
            "expected_through": target.date().isoformat(),
            "current": is_current,
        })
    return {
        "expected_through": target.date().isoformat(),
        "total_pairs": int(len(pairs)),
        "current_pairs": int(len(current_ids)),
        "stale_pairs": int(len(stale_ids)),
        "current_company_ids": current_ids,
        "stale_company_ids": stale_ids,
        "rows": rows,
    }


def sync_market_recent_history(throttle_seconds: float = 0.08) -> dict:
    """Refresh stale/missing formal daily history across the whole active universe.

    The startup path calls this before the web terminal opens.  On-demand company
    refresh remains only a fallback; it is no longer the primary freshness model.
    """
    before = market_history_audit()
    stale_ids = list(before.get("stale_company_ids") or [])
    if not stale_ids:
        return {"status": "current", "before": before, "after": before, "update": None}

    target = pd.Timestamp(before["expected_through"]).strftime("%Y%m%d")
    result = update_from_eastmoney(
        end=target,
        company_ids=stale_ids,
        throttle_seconds=max(0.0, float(throttle_seconds)),
    )
    after = market_history_audit()
    status = "current" if int(after.get("stale_pairs") or 0) == 0 else "partial"
    return {"status": status, "before": before, "after": after, "update": result}


def on_demand_sync_enabled() -> bool:
    return bool(SETTINGS.on_demand_history_sync)
