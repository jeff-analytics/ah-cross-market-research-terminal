from __future__ import annotations

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from src.analysis.premium import a_premium, shapley_contributions
from src.config import LIVE_PID_FILE, LIVE_STOP_FILE, SETTINGS
from src.data.eastmoney import EastmoneyClient
from src.data.pairs import load_pairs
from src.data.loader import load_prices
from src.data.realtime import HybridRealtimeClient
from src.market_clock import MarketState, TZ, get_market_state
from src.storage.live_store import AsyncLiveWriter, LiveStore
from src.storage.preferences import load_watchlist
from src.storage.focus_state import load_focus_ids
from src.storage.provider_settings import load_provider_settings
from src.storage.refresh_policy import apply_refresh_policy, load_refresh_policy
from src.runtime_cache import MemoryQuoteCache


@dataclass
class QueueDue:
    name: str
    interval: int
    company_ids: list[str]


def _finite(value) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _iso_age_seconds(value: str | None, now: datetime) -> float | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return max(0.0, (now.astimezone(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds())
    except ValueError:
        return None


class LiveMonitor:
    def __init__(self, client: HybridRealtimeClient | None = None, store: LiveStore | None = None) -> None:
        self._injected_client = client is not None
        self.client = client or HybridRealtimeClient()
        self.store = store or LiveStore()
        # Production queues use independent HTTP session pools. A slow universe crawl
        # therefore cannot occupy the same request objects used by the focused symbol.
        if self._injected_client:
            self.clients = {name: self.client for name in ("focus", "watchlist", "priority", "universe")}
        else:
            self.clients = {
                "focus": self.client,
                "watchlist": HybridRealtimeClient(),
                "priority": HybridRealtimeClient(),
                "universe": HybridRealtimeClient(),
            }
        self.pairs = load_pairs(active_only=True)
        self.last_run: dict[str, datetime] = {}
        self.last_state = ""
        self.fx_cache: dict | None = None
        self.daily_priority = self._load_priority_ids()
        self.provider_settings = load_provider_settings()
        self.daily_baseline = self._load_daily_baseline()
        self.daily_latest = self._load_latest_daily_rows()
        self._seed_from_daily_if_empty()
        # Hot latest state is process-resident. SQLite becomes persistence, not a
        # dependency of every calculation cycle.
        self.cache = MemoryQuoteCache(self.store.read_latest())
        self.writer = AsyncLiveWriter(self.store)


    def _load_daily_baseline(self) -> dict[str, dict]:
        """Previous completed common A/H daily bar used as the change baseline.

        After the current trading day has completed, ``prices.csv`` may already contain
        today's formal close. In that case the baseline must be the *previous* common
        session, otherwise end-of-day change would incorrectly collapse to zero.
        """
        try:
            prices = load_prices().sort_values(["company_id", "date"])
        except Exception:
            return {}
        if "data_source" in prices.columns:
            prices = prices[prices["data_source"].astype(str).str.startswith("eastmoney")].copy()
        if prices.empty:
            return {}
        prices["date"] = pd.to_datetime(prices["date"], errors="coerce")
        today = datetime.now(TZ).date()
        out: dict[str, dict] = {}
        for company_id, group in prices.groupby("company_id", sort=False):
            group = group.dropna(subset=["date"]).sort_values("date")
            if group.empty:
                continue
            latest = group.iloc[-1]
            if latest["date"].date() == today and len(group) >= 2:
                row = group.iloc[-2]
            else:
                row = latest
            a_price = _finite(row.get("a_close"))
            h_price = _finite(row.get("h_close"))
            fx_price = _finite(row.get("fx_cnh_per_hkd"))
            if a_price is None or h_price is None or fx_price is None or min(a_price, h_price, fx_price) <= 0:
                continue
            out[str(company_id)] = {
                "date": pd.Timestamp(row.get("date")).date().isoformat(),
                "a_close": a_price,
                "h_close": h_price,
                "fx_cnh_per_hkd": fx_price,
                "premium_pct": a_premium(a_price, h_price, fx_price),
                "a_volume": _finite(row.get("a_volume")),
                "h_volume": _finite(row.get("h_volume")),
                "a_amount": _finite(row.get("a_amount")),
                "h_amount": _finite(row.get("h_amount")),
            }
        return out

    def _load_latest_daily_rows(self) -> dict[str, dict]:
        """Latest formal Eastmoney daily row for close/reference display."""
        try:
            prices = load_prices().sort_values(["company_id", "date"])
        except Exception:
            return {}
        if "data_source" in prices.columns:
            prices = prices[prices["data_source"].astype(str).str.startswith("eastmoney")].copy()
        if prices.empty:
            return {}
        latest = prices.groupby("company_id", as_index=False).tail(1)
        return {str(row["company_id"]): row.to_dict() for _, row in latest.iterrows()}

    def _seed_from_daily_if_empty(self) -> None:
        """Seed all companies from formal daily data without pretending it is live.

        If the latest formal daily row belongs to today and the market is already
        closed, it is a valid end-of-day close row and is labelled ``收盘口径``.
        Otherwise it remains an explicit previous-close reference.
        """
        existing_ids = set(self.store.read_latest().get("company_id", pd.Series(dtype=str)).astype(str))
        pair_map = self.pairs.set_index("company_id")
        now = datetime.now(timezone.utc)
        now_local = now.astimezone(TZ)
        state = get_market_state(now_local)
        records: list[dict] = []
        for company_id, row in self.daily_latest.items():
            if company_id not in pair_map.index:
                continue
            pair = pair_map.loc[company_id]
            a_price = _finite(row.get("a_close")); h_price = _finite(row.get("h_close")); fx_price = _finite(row.get("fx_cnh_per_hkd"))
            if a_price is None or h_price is None or fx_price is None:
                continue
            trading_date = pd.Timestamp(row.get("date")).date()
            is_today_close = trading_date == now_local.date() and state.premium_mode in {"close", "indicative_close"}
            a_time = datetime.combine(trading_date, dt_time(15, 0), tzinfo=TZ).astimezone(timezone.utc).isoformat()
            h_time = datetime.combine(trading_date, dt_time(16, 10), tzinfo=TZ).astimezone(timezone.utc).isoformat()
            premium = a_premium(a_price, h_price, fx_price)
            baseline = self.daily_baseline.get(company_id, {})
            base_premium = _finite(baseline.get("premium_pct"))
            change = premium - base_premium if base_premium is not None else None
            source = "eastmoney_daily_close" if is_today_close else "eastmoney_previous_close"
            quality_state = "收盘口径" if is_today_close else "上一收盘"
            quality_reason = "当日正式日线收盘口径" if is_today_close else "上一完整交易日正式收盘参考"
            records.append({
                "company_id": company_id, "company_name": pair.company_name,
                "a_code": pair.a_code, "h_code": pair.h_code, "industry": pair.industry,
                "fetched_at": now.isoformat(), "a_quote_time": a_time, "h_quote_time": h_time,
                "fx_quote_time": h_time, "a_price": a_price, "h_price": h_price,
                "fx_cnh_per_hkd": fx_price, "premium_pct": premium, "premium_change_pp": change,
                "a_contribution_pp": None, "h_contribution_pp": None, "fx_contribution_pp": None,
                "a_volume": _finite(row.get("a_volume")), "h_volume": _finite(row.get("h_volume")),
                "a_amount": _finite(row.get("a_amount")), "h_amount": _finite(row.get("h_amount")),
                "a_pct_change": _finite(row.get("a_pct_change")), "h_pct_change": _finite(row.get("h_pct_change")),
                "a_change": _finite(row.get("a_change")), "h_change": _finite(row.get("h_change")),
                "a_prev_close": _finite(row.get("a_prev_close")), "h_prev_close": _finite(row.get("h_prev_close")),
                "a_open": _finite(row.get("a_open")), "h_open": _finite(row.get("h_open")),
                "a_high": _finite(row.get("a_high")), "a_low": _finite(row.get("a_low")),
                "h_high": _finite(row.get("h_high")), "h_low": _finite(row.get("h_low")),
                "market_state": "DAILY_CLOSE" if is_today_close else "PREVIOUS_CLOSE",
                "source": source, "data_age_seconds": None, "stale_flag": 0,
                "updated_queue": "daily_close", "a_source": source, "h_source": source, "fx_source": source,
                "quote_skew_seconds": None, "quality_state": quality_state, "quality_reason": quality_reason,
                "premium_mode": "close" if is_today_close else "previous_close",
                "a_session": "closed", "h_session": "closed",
                "sync_premium_pct": None, "sync_snapshot_time": None,
            })
        seeded_ids = existing_ids | {str(r["company_id"]) for r in records}
        for pair in self.pairs.itertuples(index=False):
            if str(pair.company_id) in seeded_ids:
                continue
            records.append({
                "company_id": pair.company_id, "company_name": pair.company_name,
                "a_code": pair.a_code, "h_code": pair.h_code, "industry": pair.industry,
                "fetched_at": now.isoformat(), "a_quote_time": None, "h_quote_time": None,
                "fx_quote_time": None, "a_price": None, "h_price": None, "fx_cnh_per_hkd": None,
                "premium_pct": None, "premium_change_pp": None, "a_contribution_pp": None,
                "h_contribution_pp": None, "fx_contribution_pp": None, "a_volume": None, "h_volume": None,
                "a_amount": None, "h_amount": None, "a_pct_change": None, "h_pct_change": None,
                "a_change": None, "h_change": None, "a_prev_close": None, "h_prev_close": None,
                "a_open": None, "h_open": None, "a_high": None, "a_low": None, "h_high": None, "h_low": None,
                "market_state": "PENDING_ONLINE", "source": "universe_only", "data_age_seconds": None,
                "stale_flag": 1, "updated_queue": "pending", "a_source": None, "h_source": None, "fx_source": None,
                "quote_skew_seconds": None, "quality_state": "等待行情",
                "quality_reason": "已纳入A/H公司池；等待正式行情或日线数据",
                "premium_mode": None, "a_session": None, "h_session": None,
                "sync_premium_pct": None, "sync_snapshot_time": None,
            })
        self.store.upsert_latest(records)
        self.store.set_status("seed", {"companies": len(self.pairs), "source": "formal_daily+universe", "note": "Current-day formal close is usable after market close; previous sessions remain labelled as references."})

    def _load_priority_ids(self, limit: int = 20) -> list[str]:
        """Daily research-priority queue, quality-gated and deterministic."""
        path = Path(__file__).resolve().parents[1] / "data" / "latest_results.csv"
        if path.exists():
            try:
                frame = pd.read_csv(path)
                for col in ["severity_score", "comparability_score", "company_residual_pp", "change_z", "a_premium_pct", "premium_change_pp"]:
                    if col in frame:
                        frame[col] = pd.to_numeric(frame[col], errors="coerce")
                valid = frame.copy()
                if "analysis_status" in valid:
                    valid = valid[~valid["analysis_status"].astype(str).eq("排除")]
                if "a_premium_pct" in valid:
                    valid = valid[valid["a_premium_pct"].abs().le(300) | valid["a_premium_pct"].isna()]
                if "premium_change_pp" in valid:
                    valid = valid[valid["premium_change_pp"].abs().le(60) | valid["premium_change_pp"].isna()]
                if "comparability_score" in valid:
                    strict = valid[valid["comparability_score"].fillna(0).ge(80)].copy()
                else:
                    strict = valid.copy()
                strict["_residual_abs"] = strict.get("company_residual_pp", pd.Series(0.0, index=strict.index)).abs().fillna(0)
                strict["_z_abs"] = strict.get("change_z", pd.Series(0.0, index=strict.index)).abs().fillna(0)
                if "severity_score" in strict:
                    strict = strict.sort_values(["severity_score", "_residual_abs", "_z_abs"], ascending=[False, False, False], na_position="last")
                ids = strict["company_id"].dropna().astype(str).tolist() if "company_id" in strict else []
                if len(ids) < limit and "company_id" in valid:
                    remainder = valid[~valid["company_id"].astype(str).isin(ids)].copy()
                    remainder["_residual_abs"] = remainder.get("company_residual_pp", pd.Series(0.0, index=remainder.index)).abs().fillna(0)
                    remainder["_z_abs"] = remainder.get("change_z", pd.Series(0.0, index=remainder.index)).abs().fillna(0)
                    if "severity_score" in remainder:
                        remainder = remainder.sort_values(["severity_score", "_residual_abs", "_z_abs"], ascending=[False, False, False], na_position="last")
                    ids.extend(remainder["company_id"].dropna().astype(str).tolist())
                if ids:
                    return list(dict.fromkeys(ids))[:limit]
            except Exception:
                pass
        return self.pairs["company_id"].head(limit).tolist()

    def _queues(self, state: MarketState, now: datetime) -> list[QueueDue]:
        all_ids = [str(x) for x in self.pairs["company_id"].tolist()]
        all_set = set(all_ids)
        focus = [x for x in load_focus_ids() if x in all_set][:4]
        watchlist_raw = load_watchlist(all_ids, default_count=5, default_ids=self.daily_priority[:5])
        watchlist = [x for x in watchlist_raw if x not in set(focus)]
        priority_raw = list(dict.fromkeys(self.daily_priority + watchlist_raw))[:30]
        occupied = set(focus) | set(watchlist)
        priority = [x for x in priority_raw if x not in occupied]
        occupied.update(priority)
        universe = [x for x in all_ids if x not in occupied]
        definitions = [
            QueueDue("focus", 1 if state.any_open else state.watchlist_seconds, focus),
            QueueDue("watchlist", state.watchlist_seconds, watchlist),
            QueueDue("priority", state.priority_seconds, priority),
            QueueDue("universe", state.universe_seconds, universe),
        ]
        due: list[QueueDue] = []
        for item in definitions:
            if not item.company_ids or item.interval <= 0:
                continue
            last = self.last_run.get(item.name)
            if last is None or (now - last).total_seconds() >= item.interval:
                due.append(item)
        return due

    def _pair_frame(self, company_ids: list[str]) -> pd.DataFrame:
        selected = set(company_ids)
        return self.pairs[self.pairs["company_id"].isin(selected)].copy()

    def _make_records(self, pairs: pd.DataFrame, quotes: dict[str, dict], fx: dict | None, state: MarketState, queue: str) -> tuple[list[dict], list[dict]]:
        """Build market records with explicit session semantics.

        v5.1.8 session rules:
        - BOTH continuous trading -> synchronized real-time premium.
        - Only one market trading -> indicative premium, never labelled synchronized.
        - A-share closing auction -> auction-indicative premium.
        - Lunch -> frozen morning snapshot; keep the last synchronized premium separately.
        - Pre-open -> previous completed daily close only.
        - Post-close -> end-of-day close/reference premium.

        A missing/stale open leg can never fall back to a daily cache and still be
        presented as a current cross-market premium.
        """
        now = datetime.now(timezone.utc)
        now_local = now.astimezone(TZ)
        previous_map = self.cache.latest_map(pairs["company_id"].tolist())
        records: list[dict] = []
        snapshots: list[dict] = []

        def parse_time(value: str | None) -> datetime | None:
            if not value:
                return None
            try:
                stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                return stamp.astimezone(timezone.utc)
            except ValueError:
                return None

        def same_local_day(stamp: datetime | None) -> bool:
            return stamp is not None and stamp.astimezone(TZ).date() == now_local.date()

        def online_source(value: object) -> bool:
            source = str(value or "").lower()
            return bool(source) and not any(x in source for x in ("daily_cache", "daily_close", "pending", "bootstrap", "cache"))

        def formal_close_source(value: object) -> bool:
            source = str(value or "").lower()
            return source.startswith("eastmoney_daily_close") or source.startswith("eastmoney_previous_close")

        state_labels = {
            "realtime_sync": "实时可比",
            "indicative_single_leg": "单边指示",
            "auction_indicative": "竞价指示",
            "lunch_snapshot": "午间快照",
            "previous_close": "上一收盘",
            "close": "收盘口径",
            "indicative_close": "单边收盘参考",
            "manual_snapshot": "手动快照",
        }
        reason_labels = {
            "realtime_sync": "A/H连续交易时段行情时间一致且数据新鲜",
            "indicative_single_leg": f"{state.label}；使用交易中一侧实时价与另一侧冻结/上一有效价，属于非同步指示值",
            "auction_indicative": "A股收盘集合竞价阶段；A/H指标仅作竞价指示，不纳入实时异常评分",
            "lunch_snapshot": "两市午间暂停；使用各市场上午最后有效价形成午间快照，非同步实时指标",
            "previous_close": "盘前使用上一交易日完整收盘口径；未使用集合竞价价格",
            "close": "两市已收盘；使用当日最终/冻结行情形成日终收盘口径",
            "indicative_close": "单边交易日日终参考；至少一侧当日未交易，因此不是同步收盘口径",
            "manual_snapshot": "手动全量抓取快照；用于诊断数据源，不纳入实时异常评分",
        }

        for row in pairs.itertuples(index=False):
            prior = previous_map.get(row.company_id, {})
            baseline = self.daily_baseline.get(str(row.company_id), {})
            a_quote = quotes.get(EastmoneyClient.a_secid(row.a_code))
            h_quote = quotes.get(EastmoneyClient.h_secid(row.h_code))
            mode = state.premium_mode

            if mode == "previous_close":
                a_price = _finite(baseline.get("a_close"))
                h_price = _finite(baseline.get("h_close"))
                fx_price = _finite(baseline.get("fx_cnh_per_hkd"))
                baseline_date = str(baseline.get("date") or "")
                try:
                    day = datetime.fromisoformat(baseline_date).date()
                    a_time = datetime.combine(day, dt_time(15, 0), tzinfo=TZ).astimezone(timezone.utc).isoformat()
                    h_time = datetime.combine(day, dt_time(16, 10), tzinfo=TZ).astimezone(timezone.utc).isoformat()
                    fx_time = h_time
                except ValueError:
                    a_time = h_time = fx_time = None
                a_dt, h_dt, fx_dt = parse_time(a_time), parse_time(h_time), parse_time(fx_time)
                a_source = h_source = fx_source = "daily_close"
                missing_open_leg = False
            else:
                missing_open_leg = (state.a_open and not a_quote) or (state.h_open and not h_quote)
                a_price = _finite(a_quote.get("price")) if a_quote else (None if state.a_open else _finite(prior.get("a_price")))
                h_price = _finite(h_quote.get("price")) if h_quote else (None if state.h_open else _finite(prior.get("h_price")))
                fx_price = _finite(fx.get("price")) if fx else _finite(prior.get("fx_cnh_per_hkd"))
                a_time = a_quote.get("quote_time") if a_quote else (None if state.a_open else prior.get("a_quote_time"))
                h_time = h_quote.get("quote_time") if h_quote else (None if state.h_open else prior.get("h_quote_time"))
                fx_time = (fx.get("fetched_at") or fx.get("quote_time")) if fx else prior.get("fx_quote_time")
                a_dt, h_dt, fx_dt = parse_time(a_time), parse_time(h_time), parse_time(fx_time)
                a_source = str(a_quote.get("provider") if a_quote else prior.get("a_source") or "daily_cache")
                h_source = str(h_quote.get("provider") if h_quote else prior.get("h_source") or "daily_cache")
                fx_source = str(fx.get("provider") if fx else prior.get("fx_source") or "cache")

            # Hard failures decide whether the cross-market premium can be calculated.
            # Soft notes are informational only. A public quote endpoint commonly reports
            # the *last trade* timestamp, which can be minutes old for an illiquid HK stock
            # even though the HTTP snapshot itself was fetched just now. Therefore transport
            # freshness is validated with fetched_at; last-trade time remains display metadata.
            quality_reasons: list[str] = []
            quality_notes: list[str] = []
            if missing_open_leg:
                quality_reasons.append("交易中的市场缺少实时行情")
            if a_price is None or h_price is None or fx_price is None or min(v for v in (a_price or 0, h_price or 0, fx_price or 0)) <= 0:
                quality_reasons.append("价格或汇率缺失")

            a_fetch_dt = parse_time(a_quote.get("fetched_at")) if a_quote else None
            h_fetch_dt = parse_time(h_quote.get("fetched_at")) if h_quote else None
            # Compatibility for injected/test clients that predate fetched_at. In production
            # Tencent/Sina quotes always carry fetched_at. Falling back to quote_time keeps
            # older adapters usable without weakening the production validation path.
            if a_quote and a_fetch_dt is None:
                a_fetch_dt = a_dt
            if h_quote and h_fetch_dt is None:
                h_fetch_dt = h_dt

            ages: list[float] = []
            if mode != "previous_close":
                if state.a_open and a_quote:
                    if a_fetch_dt is None:
                        quality_reasons.append("A股抓取时间缺失")
                    else:
                        age_a_transport = max(0.0, (now - a_fetch_dt).total_seconds())
                        ages.append(age_a_transport)
                        if age_a_transport > self.provider_settings.open_leg_max_age_seconds:
                            quality_reasons.append(f"A股抓取响应延迟{age_a_transport:.0f}秒")
                    if a_dt is None:
                        quality_notes.append("A股最近成交时间缺失")
                    else:
                        trade_age_a = max(0.0, (now - a_dt).total_seconds())
                        if trade_age_a > self.provider_settings.open_leg_max_age_seconds:
                            quality_notes.append(f"A股最近成交距今{trade_age_a:.0f}秒")
                if state.h_open and h_quote:
                    if h_fetch_dt is None:
                        quality_reasons.append("H股抓取时间缺失")
                    else:
                        age_h_transport = max(0.0, (now - h_fetch_dt).total_seconds())
                        ages.append(age_h_transport)
                        if age_h_transport > self.provider_settings.open_leg_max_age_seconds:
                            quality_reasons.append(f"H股抓取响应延迟{age_h_transport:.0f}秒")
                    if h_dt is None:
                        quality_notes.append("H股最近成交时间缺失")
                    else:
                        trade_age_h = max(0.0, (now - h_dt).total_seconds())
                        if trade_age_h > self.provider_settings.open_leg_max_age_seconds:
                            quality_notes.append(f"H股最近成交距今{trade_age_h:.0f}秒")

                # In ordinary one-sided windows the closed leg must be today's frozen
                # network quote if that exchange traded today. On a market holiday the
                # previous official close is an allowed reference and the result remains
                # explicitly labelled indicative.
                if state.h_open and not state.a_open and state.a_trading_day:
                    if not same_local_day(a_dt):
                        quality_reasons.append("A股冻结价不是当日行情")
                    if not online_source(a_source):
                        quality_reasons.append("A股冻结价不是在线行情")
                if state.a_open and not state.h_open and state.h_trading_day:
                    if not same_local_day(h_dt):
                        quality_reasons.append("H股冻结价不是当日行情")
                    if not online_source(h_source):
                        quality_reasons.append("H股冻结价不是在线行情")

                # Lunch and close snapshots must represent today's trading day for every
                # market that actually traded today. A market holiday is intentionally
                # allowed to retain its previous official close as a reference.
                if mode in {"lunch_snapshot", "close", "indicative_close"}:
                    if mode in {"close", "indicative_close"}:
                        a_source_ok = online_source(a_source) or formal_close_source(a_source)
                        h_source_ok = online_source(h_source) or formal_close_source(h_source)
                    else:
                        a_source_ok = online_source(a_source)
                        h_source_ok = online_source(h_source)
                    if state.a_trading_day and (not same_local_day(a_dt) or not a_source_ok):
                        quality_reasons.append("A股参考价不是当日正式收盘/在线行情" if mode in {"close", "indicative_close"} else "A股参考价不是当日在线行情")
                    if state.h_trading_day and (not same_local_day(h_dt) or not h_source_ok):
                        quality_reasons.append("H股参考价不是当日正式收盘/在线行情" if mode in {"close", "indicative_close"} else "H股参考价不是当日在线行情")

                if state.fx_open:
                    if fx_dt is None:
                        quality_reasons.append("汇率时间缺失")
                    else:
                        age_fx = max(0.0, (now - fx_dt).total_seconds())
                        close_fx_reference = mode in {"close", "indicative_close"} and formal_close_source(fx_source) and same_local_day(fx_dt)
                        if age_fx > self.provider_settings.fx_max_age_seconds and not close_fx_reference:
                            quality_reasons.append(f"汇率延迟{age_fx:.0f}秒")

            # Cross-market synchronization is a snapshot-transport property. Last-trade
            # timestamps can differ naturally when one leg has not traded for a while.
            # Compare the per-leg HTTP fetch timestamps instead of last-trade timestamps.
            skew = None
            if a_fetch_dt and h_fetch_dt:
                skew = abs((a_fetch_dt - h_fetch_dt).total_seconds())
                if mode == "realtime_sync" and skew > self.provider_settings.both_market_max_skew_seconds:
                    quality_reasons.append(f"A/H抓取时间差{skew:.0f}秒")

            def current_or_prior(quote: dict | None, key: str, prior_key: str) -> float | None:
                current = _finite(quote.get(key)) if quote else None
                return current if current is not None else _finite(prior.get(prior_key))

            # OHLC fields enrich the terminal card but are not required to calculate an
            # A/H premium. Preserve the previous same-session value when a provider omits
            # a field on one snapshot, and downgrade a first-snapshot omission to a note.
            a_high = current_or_prior(a_quote, "high", "a_high")
            a_low = current_or_prior(a_quote, "low", "a_low")
            h_high = current_or_prior(h_quote, "high", "h_high")
            h_low = current_or_prior(h_quote, "low", "h_low")
            if state.a_open and (not a_high or not a_low):
                quality_notes.append("A股盘中高低价暂缺，仅影响展示")
            if state.h_open and (not h_high or not h_low):
                quality_notes.append("H股盘中高低价暂缺，仅影响展示")
            if mode == "previous_close":
                a_high = a_low = h_high = h_low = None

            usable = not quality_reasons
            premium = None
            change = None
            contributions = {"a_contribution_pp": None, "h_contribution_pp": None, "fx_contribution_pp": None}
            if usable and a_price is not None and h_price is not None and fx_price is not None:
                premium = a_premium(a_price, h_price, fx_price)
                if mode != "previous_close":
                    baseline_premium = _finite(baseline.get("premium_pct"))
                    if baseline_premium is not None:
                        change = premium - baseline_premium
                # Attribution and live anomaly semantics only apply to synchronized
                # continuous trading. Indicative/lunch/auction/close modes are displayed
                # but deliberately excluded from this decomposition.
                if mode == "realtime_sync":
                    base_a = _finite(baseline.get("a_close"))
                    base_h = _finite(baseline.get("h_close"))
                    base_fx = _finite(baseline.get("fx_cnh_per_hkd"))
                    if None not in (base_a, base_h, base_fx):
                        previous_series = pd.Series({"a_close": base_a, "h_close": base_h, "fx_cnh_per_hkd": base_fx})
                        current_series = pd.Series({"a_close": a_price, "h_close": h_price, "fx_cnh_per_hkd": fx_price})
                        contributions = shapley_contributions(previous_series, current_series)

            sync_premium = _finite(prior.get("sync_premium_pct"))
            sync_time = prior.get("sync_snapshot_time")
            if usable and mode == "realtime_sync" and premium is not None:
                sync_premium = premium
                # The synchronized snapshot time reflects when both legs were fetched, not
                # when the less-liquid security last happened to trade.
                candidates = [x for x in (a_fetch_dt, h_fetch_dt) if x is not None]
                sync_time = max(candidates).isoformat() if candidates else now.isoformat()

            age = max(ages) if ages else None
            source = f"A:{a_source}|H:{h_source}|FX:{fx_source}"
            quality_state = state_labels.get(mode, "实时可比") if usable else "暂停计算"
            if quality_reasons:
                quality_reason = "；".join(dict.fromkeys(quality_reasons + quality_notes))
            elif quality_notes:
                quality_reason = f"{reason_labels.get(mode, state.label)}；" + "；".join(dict.fromkeys(quality_notes))
            else:
                quality_reason = reason_labels.get(mode, state.label)

            if mode == "previous_close":
                a_volume = _finite(baseline.get("a_volume"))
                h_volume = _finite(baseline.get("h_volume"))
                a_amount = _finite(baseline.get("a_amount"))
                h_amount = _finite(baseline.get("h_amount"))
            else:
                a_volume = _finite(a_quote.get("volume")) if a_quote else _finite(prior.get("a_volume"))
                h_volume = _finite(h_quote.get("volume")) if h_quote else _finite(prior.get("h_volume"))
                a_amount = _finite(a_quote.get("amount")) if a_quote else _finite(prior.get("a_amount"))
                h_amount = _finite(h_quote.get("amount")) if h_quote else _finite(prior.get("h_amount"))

            record = {
                "company_id": row.company_id, "company_name": row.company_name,
                "a_code": row.a_code, "h_code": row.h_code, "industry": row.industry,
                "fetched_at": now.isoformat(), "a_quote_time": a_time, "h_quote_time": h_time, "fx_quote_time": fx_time,
                "a_price": a_price, "h_price": h_price, "fx_cnh_per_hkd": fx_price,
                "premium_pct": premium, "premium_change_pp": change, **contributions,
                "a_volume": a_volume, "h_volume": h_volume, "a_amount": a_amount, "h_amount": h_amount,
                "a_pct_change": None if mode == "previous_close" else (_finite(a_quote.get("pct_change")) if a_quote else _finite(prior.get("a_pct_change"))),
                "h_pct_change": None if mode == "previous_close" else (_finite(h_quote.get("pct_change")) if h_quote else _finite(prior.get("h_pct_change"))),
                "a_change": None if mode == "previous_close" else (_finite(a_quote.get("change")) if a_quote else _finite(prior.get("a_change"))),
                "h_change": None if mode == "previous_close" else (_finite(h_quote.get("change")) if h_quote else _finite(prior.get("h_change"))),
                "a_prev_close": _finite(a_quote.get("prev_close")) if a_quote else _finite(baseline.get("a_close")),
                "h_prev_close": _finite(h_quote.get("prev_close")) if h_quote else _finite(baseline.get("h_close")),
                "a_open": None if mode == "previous_close" else (_finite(a_quote.get("open")) if a_quote else _finite(prior.get("a_open"))),
                "h_open": None if mode == "previous_close" else (_finite(h_quote.get("open")) if h_quote else _finite(prior.get("h_open"))),
                "a_high": a_high, "a_low": a_low, "h_high": h_high, "h_low": h_low,
                "market_state": state.code, "source": source, "data_age_seconds": age,
                "stale_flag": 0 if usable else 1, "updated_queue": queue,
                "a_source": a_source, "h_source": h_source, "fx_source": fx_source,
                "quote_skew_seconds": skew, "quality_state": quality_state, "quality_reason": quality_reason,
                "premium_mode": mode, "a_session": state.a_session, "h_session": state.h_session,
                "sync_premium_pct": sync_premium, "sync_snapshot_time": sync_time,
            }
            records.append(record)

            if usable and premium is not None and mode != "previous_close":
                if queue == "watchlist":
                    bucket_seconds = max(1, min(state.store_seconds, state.watchlist_seconds))
                elif queue == "priority":
                    bucket_seconds = max(1, min(state.store_seconds, state.priority_seconds))
                else:
                    bucket_seconds = max(5, min(state.store_seconds, state.universe_seconds))
                epoch = int(now.timestamp())
                bucket_epoch = epoch - (epoch % bucket_seconds)
                snapshots.append({
                    "company_id": row.company_id, "snapshot_time": now.isoformat(),
                    "snapshot_bucket": datetime.fromtimestamp(bucket_epoch, tz=timezone.utc).isoformat(),
                    "a_price": a_price, "h_price": h_price, "fx_cnh_per_hkd": fx_price,
                    "premium_pct": premium, "premium_change_pp": change, "market_state": state.code,
                    "trigger_reason": queue, "source": source,
                })
                self._maybe_alert(record, prior)
        return records, snapshots

    def _maybe_alert(self, record: dict, prior: dict) -> None:
        # Only synchronized continuous-trading observations feed the live anomaly
        # alert channel. Single-leg / auction / lunch / close values remain visible
        # as research context but are not mixed with real-time anomalies.
        if record.get("quality_state") != "实时可比":
            return
        change = float(record.get("premium_change_pp") or 0.0)
        if record.get("stale_flag"):
            return
        level = 0
        if abs(change) >= 2.0:
            level = 2
        if abs(change) >= 4.0:
            level = 3
        if level == 0:
            return
        direction = "扩大" if change > 0 else "收窄"
        last = self.store.last_alert(record["company_id"], "premium_move")
        suppressed = False
        if last:
            try:
                elapsed = datetime.now(timezone.utc) - datetime.fromisoformat(last["created_at"].replace("Z", "+00:00"))
                same_direction = str(last.get("direction")) == direction
                no_escalation = int(last.get("level") or 0) >= level
                suppressed = elapsed < timedelta(minutes=15) and same_direction and no_escalation
            except ValueError:
                pass
        message = f"盘中A/H溢价{direction} {change:+.2f}pp，当前 {record['premium_pct']:.2f}%"
        self.store.record_alert(record["company_id"], "premium_move", direction, level, record["premium_pct"], change, message, suppressed)

    def refresh_queue(self, item: QueueDue, state: MarketState) -> dict:
        pairs = self._pair_frame(item.company_ids)
        if pairs.empty:
            return {"queue": item.name, "updated": 0}
        # Single-leg, lunch and post-close windows still request both stock legs so
        # the closed side can be captured as a same-day frozen quote. PRE_OPEN is the
        # exception: auction indications are intentionally ignored and the terminal
        # shows the previous completed daily close instead.
        close_refresh = state.premium_mode in {"close", "indicative_close"}
        fetch_a = state.code != "PRE_OPEN" and (state.code != "CLOSED" or close_refresh)
        fetch_h = state.code != "PRE_OPEN" and (state.code != "CLOSED" or close_refresh)
        fetch_fx = state.fx_open and state.code != "PRE_OPEN"
        queue_client = self.clients.get(item.name, self.client)
        started = time.perf_counter()
        quotes, fx = queue_client.fetch_pair_quotes(pairs, fetch_a, fetch_h, fetch_fx)
        if fx:
            self.fx_cache = fx
        else:
            fx = self.fx_cache
        records, snapshots = self._make_records(pairs, quotes, fx, state, item.name)
        # Memory is updated first. Persistence happens on the dedicated writer thread.
        self.cache.update(records)
        provider_status = getattr(queue_client, "last_status", {})
        self.writer.submit_status("providers", provider_status)
        self.writer.submit_latest(records)
        self.writer.submit_snapshots(snapshots)
        # Injected clients are used by deterministic tests/diagnostics that expect
        # refresh_queue() to be a synchronous boundary. Production queue clients keep
        # the non-blocking path.
        if self._injected_client:
            self.writer.flush(timeout=5.0)
        self.last_run[item.name] = datetime.now(TZ)
        return {
            "queue": item.name, "requested": len(pairs), "updated": len(records),
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 2),
        }

    def run_once(self, now: datetime | None = None, force: bool = False) -> dict:
        now = now.astimezone(TZ) if now is not None else datetime.now(TZ)
        state = apply_refresh_policy(get_market_state(now), load_refresh_policy())
        if force:
            state = MarketState(
                "FORCED", "手动全量快照", True, True, True, 5, 15, 60, 30,
                premium_mode="manual_snapshot", a_session="reference", h_session="reference",
                a_trading_day=state.a_trading_day, h_trading_day=state.h_trading_day,
            )
        self.writer.submit_status("heartbeat", {"local_time": now.isoformat(), "state": state.code, "label": state.label, "source": "hybrid_realtime_memory"})
        self.writer.submit_status("crawler", {"state": "running", "source": "hybrid", "mode": "parallel_adaptive", "market_state": state.code})
        allow_close_refresh = state.premium_mode in {"close", "indicative_close"} and (state.a_trading_day or state.h_trading_day)
        if not state.any_open and state.code not in {"PRE_OPEN", "LUNCH", "BOTH_BREAK", "POST_CLOSE", "A_CLOSED_H_HOLIDAY"} and not allow_close_refresh and not force:
            result = {"state": state.code, "label": state.label, "queues": [], "message": "No active refresh outside market hours."}
            self.writer.submit_status("last_cycle", result)
            self.writer.flush(timeout=2.0)
            return result
        queues = self._queues(state, now)
        if force and not queues:
            queues = [QueueDue("universe", max(1, state.universe_seconds or 60), self.pairs["company_id"].tolist())]
        outputs: list[dict] = []
        errors: list[dict] = []
        refreshed_in_cycle: set[str] = set()
        for item in queues:
            remaining = [company_id for company_id in item.company_ids if company_id not in refreshed_in_cycle]
            if not remaining:
                self.last_run[item.name] = now
                continue
            effective = QueueDue(item.name, item.interval, remaining)
            try:
                outputs.append(self.refresh_queue(effective, state))
                refreshed_in_cycle.update(remaining)
            except Exception as exc:
                errors.append({"queue": item.name, "error": str(exc)})
        result = {"state": state.code, "label": state.label, "queues": outputs, "errors": errors, "time": now.isoformat()}
        self.writer.submit_status("last_cycle", result)
        self.writer.submit_status("crawler", {"state": "running" if not errors else "degraded", "source": "hybrid", "mode": "parallel_adaptive", "updated": sum(int(x.get("updated", 0)) for x in outputs), "errors": errors, "cycle_time": now.isoformat(), "memory": self.cache.meta(), "writer": self.writer.metrics()})
        # run_once is a synchronous API/test boundary. The long-running scheduler below
        # does not wait for SQLite.
        self.writer.flush(timeout=5.0)
        return result

    def run_forever(self, sleep_seconds: float = 1.0, max_cycles: int | None = None) -> None:
        """Run independent focus/watchlist/priority/universe schedulers.

        Queue fetches run in parallel with dedicated provider sessions. A slow full
        universe request can no longer delay the focused company or watchlist queue.
        """
        import os

        LIVE_STOP_FILE.unlink(missing_ok=True)
        LIVE_PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        self.writer.submit_status("monitor", {"state": "running", "pid": os.getpid(), "source": "hybrid_realtime_memory"})
        tick_seconds = max(0.05, min(float(sleep_seconds), 0.20))
        cycles = 0
        inflight: dict[str, object] = {}
        last_heartbeat = 0.0
        last_report = 0.0
        executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ah-scheduler")
        try:
            while not LIVE_STOP_FILE.exists():
                now = datetime.now(TZ)
                state = apply_refresh_policy(get_market_state(now), load_refresh_policy())
                mono = time.monotonic()

                # Harvest completed queue jobs without blocking the scheduler.
                completed: list[dict] = []
                errors: list[dict] = []
                for name, future in list(inflight.items()):
                    if not future.done():
                        continue
                    inflight.pop(name, None)
                    try:
                        completed.append(future.result())
                    except Exception as exc:
                        errors.append({"queue": name, "error": str(exc)})

                allow_close_refresh = state.premium_mode in {"close", "indicative_close"} and (state.a_trading_day or state.h_trading_day)
                active_window = state.any_open or state.code in {"PRE_OPEN", "LUNCH", "BOTH_BREAK", "POST_CLOSE", "A_CLOSED_H_HOLIDAY"} or allow_close_refresh
                if active_window:
                    for item in self._queues(state, now):
                        if item.name in inflight:
                            continue
                        # Mark dispatch time immediately; if a request runs long the same
                        # queue is not duplicated, while other queues remain independent.
                        self.last_run[item.name] = now
                        inflight[item.name] = executor.submit(self.refresh_queue, item, state)

                if mono - last_heartbeat >= 1.0:
                    last_heartbeat = mono
                    self.writer.submit_status("heartbeat", {
                        "local_time": now.isoformat(), "state": state.code, "label": state.label,
                        "source": "hybrid_realtime_memory", "in_flight": sorted(inflight),
                    })

                if completed or errors or mono - last_report >= 2.0:
                    last_report = mono
                    crawler = {
                        "state": "degraded" if errors else "running",
                        "source": "hybrid", "mode": "parallel_adaptive",
                        "completed": completed, "errors": errors,
                        "in_flight": sorted(inflight), "cycle_time": now.isoformat(),
                        "memory": self.cache.meta(), "writer": self.writer.metrics(),
                    }
                    self.writer.submit_status("crawler", crawler)
                    if completed or errors:
                        self.writer.submit_status("last_cycle", {
                            "state": state.code, "label": state.label, "queues": completed,
                            "errors": errors, "time": now.isoformat(),
                        })
                        print(json.dumps(crawler, ensure_ascii=False), flush=True)
                        cycles += 1
                        if max_cycles is not None and cycles >= max_cycles:
                            break

                time.sleep(tick_seconds)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
            self.writer.flush(timeout=5.0)
            self.writer.close(timeout=3.0)
            LIVE_PID_FILE.unlink(missing_ok=True)
            self.store.set_status("monitor", "stopped")
