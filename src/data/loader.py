from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Iterable

import pandas as pd

from src.config import PRICES_FILE, SETTINGS, UPDATE_LOG_FILE
from src.data.demo import generate_demo_data
from src.data.ecb_fx import EcbFxClient
from src.data.eastmoney import EastmoneyClient
from src.data.pairs import load_pairs

REQUIRED_PRICE_COLUMNS = {
    "date", "company_id", "a_close", "h_close", "fx_cnh_per_hkd", "a_volume", "h_volume",
}


def load_prices() -> pd.DataFrame:
    if not PRICES_FILE.exists():
        generate_demo_data()
    frame = pd.read_csv(PRICES_FILE, parse_dates=["date"], dtype={"a_code": str, "h_code": str})
    missing = REQUIRED_PRICE_COLUMNS.difference(frame.columns)
    if missing:
        raise ValueError(f"Price dataset missing columns: {sorted(missing)}")
    frame = frame.sort_values(["company_id", "date"]).reset_index(drop=True)
    return frame


def _real_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if "data_source" not in frame.columns:
        return frame
    return frame[frame["data_source"].astype(str).str.startswith("eastmoney")].copy()


def _default_history_start(end_date: str) -> str:
    end_ts = pd.Timestamp(end_date)
    start_ts = end_ts - pd.DateOffset(years=SETTINGS.daily_history_years)
    return start_ts.strftime("%Y%m%d")


def _start_for_company(existing: pd.DataFrame, company_id: str, default_start: str) -> str:
    company = existing.loc[existing["company_id"].eq(company_id)].copy()
    company = _real_rows(company)
    dates = company["date"] if not company.empty else pd.Series(dtype="datetime64[ns]")
    if dates.empty:
        return default_start.replace("-", "")
    last = pd.to_datetime(dates).max() - pd.Timedelta(days=10)
    return last.strftime("%Y%m%d")


def _valid_fx(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["date", "fx_cnh_per_hkd", "fx_source"])
    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["fx_cnh_per_hkd"] = pd.to_numeric(out["fx_cnh_per_hkd"], errors="coerce")
    out = out.dropna(subset=["date", "fx_cnh_per_hkd"])
    out = out[out["fx_cnh_per_hkd"].between(0.5, 1.5)].copy()
    if "fx_source" not in out.columns:
        out["fx_source"] = "unknown_fx"
    return out.sort_values("date")


def _fetch_historical_fx(client: EastmoneyClient, start: str, end: str) -> tuple[pd.DataFrame, list[dict[str, str]]]:
    """Fetch daily HKD/CNY with an official ECB fallback.

    Eastmoney remains primary so historical conversion stays consistent with the
    existing project.  If its FX series fails or is stale near the requested end,
    ECB CNY/EUR and HKD/EUR reference rates are crossed to CNY/HKD and used to
    supplement missing dates.  Price history is never fabricated.
    """
    issues: list[dict[str, str]] = []
    primary = pd.DataFrame()
    try:
        primary = _valid_fx(client.fetch_fx(start, end))
    except Exception as exc:
        issues.append({"provider": "eastmoney_fx_daily", "error": str(exc)})

    target = pd.Timestamp(str(end).replace("-", "")) if str(end).replace("-", "").isdigit() else pd.NaT
    need_fallback = primary.empty
    if not primary.empty and pd.notna(target):
        need_fallback = (target - primary["date"].max()).days > 1

    fallback = pd.DataFrame()
    if need_fallback:
        try:
            fallback = _valid_fx(EcbFxClient().fetch_fx(start, end))
        except Exception as exc:
            issues.append({"provider": "ecb_reference_cross", "error": str(exc)})

    if primary.empty and fallback.empty:
        detail = "; ".join(f"{x['provider']}: {x['error']}" for x in issues) or "no FX data"
        raise RuntimeError(f"Historical HKD/CNY unavailable ({detail})")

    # Keep ECB first and Eastmoney last so Eastmoney wins duplicate dates while ECB
    # fills days that are missing/stale in the primary series.
    pieces = [x for x in [fallback, primary] if not x.empty]
    combined = pd.concat(pieces, ignore_index=True, sort=False)
    combined = combined.sort_values(["date", "fx_source"]).drop_duplicates("date", keep="last")
    return combined[["date", "fx_cnh_per_hkd", "fx_source"]].sort_values("date"), issues


def _attach_fx(pair: pd.DataFrame, fx: pd.DataFrame) -> pd.DataFrame:
    if pair.empty:
        return pair
    left = pair.sort_values("date").copy()
    right = fx.sort_values("date").copy()
    # FX and equity calendars are not identical. Use the latest published reference
    # rate on or before each common A/H trading day, with a one-week safety limit.
    merged = pd.merge_asof(
        left,
        right,
        on="date",
        direction="backward",
        tolerance=pd.Timedelta(days=7),
    )
    # The earliest equity row can precede the first FX observation in a narrow
    # request. A forward fill is allowed only within the same seven-day tolerance.
    missing = merged["fx_cnh_per_hkd"].isna()
    if missing.any():
        forward = pd.merge_asof(
            left.loc[missing, ["date"]].sort_values("date"),
            right,
            on="date",
            direction="forward",
            tolerance=pd.Timedelta(days=7),
        ).set_index("date")
        for idx in merged.index[missing]:
            day = merged.at[idx, "date"]
            if day in forward.index:
                merged.at[idx, "fx_cnh_per_hkd"] = forward.at[day, "fx_cnh_per_hkd"]
                merged.at[idx, "fx_source"] = forward.at[day, "fx_source"]
    return merged.dropna(subset=["fx_cnh_per_hkd"])


def update_from_eastmoney(
    start: str | None = None,
    end: str | None = None,
    company_ids: Iterable[str] | None = None,
    throttle_seconds: float = 0.35,
    force_start: bool = False,
) -> dict[str, object]:
    """Incrementally update active A/H pairs with formal daily history.

    A/H closes are sourced from Eastmoney's unadjusted daily endpoint with host and
    cache-bypass retries. Historical HKD/CNY is Eastmoney-primary with an official
    ECB reference-rate fallback. Failed instruments never delete existing rows.
    """
    end_date = pd.Timestamp(end or pd.Timestamp.today()).strftime("%Y%m%d")
    start = start or _default_history_start(end_date)
    pairs = load_pairs(active_only=True)
    if company_ids:
        selected = {str(x) for x in company_ids}
        pairs = pairs[pairs["company_id"].astype(str).isin(selected)]
    existing = load_prices()
    client = EastmoneyClient()

    global_start = start.replace("-", "") if force_start else min(
        (_start_for_company(existing, str(cid), start) for cid in pairs["company_id"]),
        default=start.replace("-", ""),
    )
    outputs: list[pd.DataFrame] = []
    failures: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    lagging_pairs: list[dict[str, str]] = []
    started_at = datetime.now(timezone.utc)

    try:
        fx, fx_issues = _fetch_historical_fx(client, global_start, end_date)
        warnings.extend(fx_issues)
    except Exception as exc:
        result: dict[str, object] = {
            "status": "failed",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "requested_pairs": len(pairs),
            "updated_pairs": 0,
            "failed_pairs": [{"company_id": "FX", "company_name": "HKD/CNY", "error": str(exc)}],
            "warnings": warnings,
            "real_data_companies": int(existing.loc[existing.get("data_source", pd.Series(index=existing.index, dtype=str)).eq("eastmoney"), "company_id"].nunique()) if "data_source" in existing else 0,
            "local_companies": int(existing["company_id"].nunique()),
            "history_60_plus": int((existing.groupby("company_id").size() >= 60).sum()),
            "data_mode": "unchanged",
            "start": start,
            "end": end_date,
            "source": "eastmoney_ah+fx_fallback",
            "error_stage": "fx",
            "elapsed_seconds": (datetime.now(timezone.utc) - started_at).total_seconds(),
        }
        UPDATE_LOG_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    target_ts = pd.Timestamp(end_date)
    for idx, row in enumerate(pairs.itertuples(index=False), start=1):
        company_id = str(row.company_id)
        pair_start = start.replace("-", "") if force_start else _start_for_company(existing, company_id, start)
        try:
            pair = client.fetch_pair(row.a_code, row.h_code, pair_start, end_date)
            if pair.empty:
                raise RuntimeError("No common A/H daily bars returned")
            merged = _attach_fx(pair, fx)
            if merged.empty:
                raise RuntimeError("No A/H rows could be matched to a valid historical HKD/CNY rate")
            pair_latest = pd.to_datetime(merged["date"], errors="coerce").max()
            lag_days = (target_ts - pair_latest).days if pd.notna(pair_latest) else 9999
            if lag_days > 1:
                # A provider response can be syntactically successful yet still stop
                # before the requested completed session (for example 08-05 when
                # 08-07 is already complete).  Track that separately so a universe
                # crawl can never be certified as fully current just because every
                # HTTP request returned rows.
                lag_item = {
                    "provider": "eastmoney_ah_daily",
                    "company_id": company_id,
                    "company_name": str(row.company_name),
                    "latest": pair_latest.date().isoformat() if pd.notna(pair_latest) else None,
                    "expected_through": target_ts.date().isoformat(),
                    "error": f"latest common A/H bar {pair_latest.date().isoformat()} is {lag_days} calendar days behind requested {target_ts.date().isoformat()}",
                }
                lagging_pairs.append(lag_item)
                warnings.append(lag_item)

            merged["company_id"] = company_id
            merged["company_name"] = row.company_name
            merged["a_code"] = row.a_code
            merged["h_code"] = row.h_code
            merged["a_ticker"] = row.a_ticker
            merged["h_ticker"] = row.h_ticker
            merged["industry"] = row.industry
            merged["ex_dividend_h"] = 0
            merged["single_side_halt"] = (
                (merged["h_volume"].fillna(0).eq(0) & merged["a_volume"].fillna(0).gt(0))
                | (merged["a_volume"].fillna(0).eq(0) & merged["h_volume"].fillna(0).gt(0))
            ).astype(int)
            merged["data_source"] = "eastmoney"
            outputs.append(merged)
        except Exception as exc:
            failures.append({"company_id": company_id, "company_name": row.company_name, "error": str(exc)})
        if idx < len(pairs):
            time.sleep(max(0.0, throttle_seconds))

    refreshed_screen = pd.DataFrame()
    if outputs:
        new_data = pd.concat(outputs, ignore_index=True)
        success_ids = set(new_data["company_id"].astype(str))

        # Re-read immediately before commit because background and on-demand updates
        # can overlap on both Windows and macOS.
        current = load_prices()
        current_ids = current["company_id"].astype(str)
        untouched = current[~current_ids.isin(success_ids)].copy()
        prior_success = _real_rows(current[current_ids.isin(success_ids)].copy())
        combined_success = pd.concat([prior_success, new_data], ignore_index=True, sort=False)
        combined_success = combined_success.sort_values(["company_id", "date", "data_source"]).drop_duplicates(
            ["company_id", "date"], keep="last"
        )
        combined = pd.concat([untouched, combined_success], ignore_index=True, sort=False)
        combined = combined.sort_values(["company_id", "date"])

        tmp_prices = PRICES_FILE.with_name(f"{PRICES_FILE.name}.tmp.{os.getpid()}")
        combined.to_csv(tmp_prices, index=False, encoding="utf-8-sig")
        os.replace(tmp_prices, PRICES_FILE)

        from src.analysis.screener import build_screener
        from src.config import RESULTS_FILE
        refreshed_screen, _ = build_screener(combined)
        tmp_results = RESULTS_FILE.with_name(f"{RESULTS_FILE.name}.tmp.{os.getpid()}")
        refreshed_screen.to_csv(tmp_results, index=False, encoding="utf-8-sig")
        os.replace(tmp_results, RESULTS_FILE)

    updated_ids = {str(frame["company_id"].iloc[0]) for frame in outputs}
    final = load_prices()
    real_ids = set(final.loc[final["data_source"].eq("eastmoney"), "company_id"].astype(str).unique())
    sufficient = int((_real_rows(final).groupby("company_id").size() >= 60).sum()) if not _real_rows(final).empty else 0
    complete = bool(outputs) and len(updated_ids) == len(pairs) and not failures and not lagging_pairs
    result = {
        "status": "success" if complete else ("partial" if outputs else "failed"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "requested_pairs": len(pairs),
        "updated_pairs": len(updated_ids),
        "failed_pairs": failures,
        "lagging_pairs": lagging_pairs,
        "warnings": warnings,
        "real_data_companies": len(real_ids),
        "local_companies": int(final["company_id"].nunique()),
        "history_60_plus": sufficient,
        "data_mode": "formal" if len(real_ids) == len(pairs) else ("partial" if real_ids else "demo"),
        "start": start,
        "end": end_date,
        "source": "eastmoney_ah+eastmoney_or_ecb_fx",
        "analysis_rows": int(len(refreshed_screen)) if outputs else 0,
        "analysis_refreshed": bool(outputs),
        "elapsed_seconds": (datetime.now(timezone.utc) - started_at).total_seconds(),
    }
    UPDATE_LOG_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
