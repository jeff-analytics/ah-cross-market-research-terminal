from __future__ import annotations

import gzip
import json
import os
import random
import re
import threading
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.config import SOURCE_DIR
from src.data.eastmoney import EastmoneyClient
from src.storage.provider_settings import load_provider_settings

CN_TZ = ZoneInfo("Asia/Shanghai")


def _num(value) -> float | None:
    if value in (None, "", "-", "--", "N/A"):
        return None
    try:
        number = float(value)
        return number
    except (TypeError, ValueError):
        return None


def _positive_or_none(value) -> float | None:
    number = _num(value)
    if number is None or number <= 0:
        return None
    return number


def _local_time_to_utc_iso(value: str | None, fallback: datetime | None = None) -> str:
    fallback = fallback or datetime.now(timezone.utc)
    text = str(value or "").strip()
    for fmt in ("%Y%m%d%H%M%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M:%S.%f"):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=CN_TZ)
            return dt.astimezone(timezone.utc).isoformat()
        except ValueError:
            continue
    return fallback.isoformat()


class _HttpSession:
    def __init__(self, timeout: float = 6.0) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        retry = Retry(
            total=1,
            connect=1,
            read=1,
            backoff_factor=0.05,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=32, pool_maxsize=32)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
            "Accept": "*/*",
        })


class TencentRealtimeClient(_HttpSession):
    """Near-real-time A/H snapshots from Tencent Finance public quote endpoint.

    The endpoint returns exchange-side timestamps in field 30. Those timestamps are
    preserved and used for freshness/skew validation. This client is a practical
    public-HTTP source, not an exchange-grade entitlement feed.
    """

    URL = "https://qt.gtimg.cn/q="

    @staticmethod
    def _a_symbol(code: str) -> str:
        code = str(code).strip().zfill(6)
        if code.startswith(("4", "8")):
            return f"bj{code}"
        return f"sh{code}" if code.startswith(("5", "6", "9")) else f"sz{code}"

    @staticmethod
    def _h_symbol(code: str) -> str:
        return f"r_hk{str(code).strip().zfill(5)}"

    @staticmethod
    def _canonical_key(symbol: str, fields: list[str]) -> str | None:
        if symbol.startswith("r_hk") or symbol.startswith("hk"):
            code = str(fields[2] if len(fields) > 2 else symbol[-5:]).zfill(5)
            return EastmoneyClient.h_secid(code)
        if symbol.startswith(("sh", "sz", "bj")):
            code = str(fields[2] if len(fields) > 2 else symbol[-6:]).zfill(6)
            return EastmoneyClient.a_secid(code)
        return None

    @staticmethod
    def _parse_line(line: str, fetched_at: datetime) -> tuple[str, dict] | None:
        match = re.search(r"v_([A-Za-z0-9_]+)=\"(.*)\";?", line.strip())
        if not match:
            return None
        symbol = match.group(1)
        fields = match.group(2).split("~")
        if len(fields) < 35:
            return None
        key = TencentRealtimeClient._canonical_key(symbol, fields)
        if not key:
            return None
        quote_time = _local_time_to_utc_iso(fields[30] if len(fields) > 30 else None, fetched_at)
        price = _positive_or_none(fields[3] if len(fields) > 3 else None)
        if price is None:
            return None
        # Tencent uses different trade-stat units for mainland A shares and
        # Hong Kong shares. Normalize at the provider boundary so every downstream
        # component sees the same contract:
        #   volume -> shares
        #   amount -> local-currency base units (CNY for A, HKD for H)
        is_h = symbol.startswith(("r_hk", "hk"))
        raw_volume = _num(fields[6] if len(fields) > 6 else None)
        if is_h:
            # HK quote volume is already reported in shares and field 37 is HKD.
            volume = raw_volume
            amount = _num(fields[37] if len(fields) > 37 else None)
        else:
            # Mainland quote volume is reported in lots (手, 100 shares).
            volume = raw_volume * 100.0 if raw_volume is not None else None
            # The composite field 35 carries price/volume/exact-turnover. Prefer its
            # exact CNY turnover. Field 37 is only a 10k-CNY display value.
            amount = None
            if len(fields) > 35:
                deal = str(fields[35] or "").split("/")
                if len(deal) >= 3:
                    amount = _num(deal[2])
            if amount is None:
                amount_10k = _num(fields[37] if len(fields) > 37 else None)
                amount = amount_10k * 10000.0 if amount_10k is not None else None
            if amount is None and len(fields) > 11:
                # Last-resort compatibility only; this field is not preferred.
                amount = _num(fields[11])
        record = {
            "secid": key,
            "code": str(fields[2] if len(fields) > 2 else ""),
            "name": fields[1] if len(fields) > 1 else "",
            "price": price,
            "pct_change": _num(fields[32] if len(fields) > 32 else None),
            "change": _num(fields[31] if len(fields) > 31 else None),
            "volume": volume,
            "amount": amount,
            "high": _positive_or_none(fields[33] if len(fields) > 33 else None),
            "low": _positive_or_none(fields[34] if len(fields) > 34 else None),
            "open": _positive_or_none(fields[5] if len(fields) > 5 else None),
            "prev_close": _positive_or_none(fields[4] if len(fields) > 4 else None),
            "quote_time": quote_time,
            "fetched_at": fetched_at.isoformat(),
            "provider": "tencent_http",
        }
        return key, record

    def fetch_symbols(self, symbols: Iterable[str], batch_size: int = 80) -> dict[str, dict]:
        symbols = [str(item) for item in dict.fromkeys(symbols) if item]
        output: dict[str, dict] = {}
        for idx in range(0, len(symbols), max(1, int(batch_size))):
            batch = symbols[idx:idx + batch_size]
            fetched_at = datetime.now(timezone.utc)
            response = self.session.get(self.URL + ",".join(batch), timeout=self.timeout)
            response.raise_for_status()
            response.encoding = "gbk"
            for line in response.text.splitlines():
                parsed = self._parse_line(line, fetched_at)
                if parsed:
                    output[parsed[0]] = parsed[1]
        return output

    def fetch_a(self, codes: Iterable[str], batch_size: int = 80) -> dict[str, dict]:
        return self.fetch_symbols((self._a_symbol(code) for code in codes), batch_size=batch_size)

    def fetch_h(self, codes: Iterable[str], batch_size: int = 80) -> dict[str, dict]:
        return self.fetch_symbols((self._h_symbol(code) for code in codes), batch_size=batch_size)


class SinaARealtimeClient(_HttpSession):
    """A-share near-real-time fallback from Sina Finance public quote endpoint.

    Sina exposes an exchange-side date/time in the payload. It is used only when
    Tencent fails; if both public A-share sources fail the monitor pauses the A/H
    premium instead of substituting a daily close.
    """

    URL = "https://hq.sinajs.cn/list="

    def __init__(self, timeout: float = 6.0) -> None:
        super().__init__(timeout=timeout)
        self.session.headers.update({"Referer": "https://finance.sina.com.cn/"})

    @staticmethod
    def _symbol(code: str) -> str:
        code = str(code).strip().zfill(6)
        if code.startswith(("4", "8")):
            return f"bj{code}"
        return f"sh{code}" if code.startswith(("5", "6", "9")) else f"sz{code}"

    @staticmethod
    def _parse_line(line: str, fetched_at: datetime) -> tuple[str, dict] | None:
        match = re.search(r'var\s+hq_str_([A-Za-z0-9_]+)="(.*)";?', line.strip())
        if not match:
            return None
        symbol = match.group(1)
        fields = match.group(2).split(",")
        if len(fields) < 32 or not symbol.startswith(("sh", "sz", "bj")):
            return None
        code = symbol[-6:]
        price = _positive_or_none(fields[3])
        if price is None:
            return None
        prev = _positive_or_none(fields[2])
        stamp = f"{fields[30]} {fields[31]}" if fields[30] and fields[31] else None
        change = price - prev if prev else None
        pct = change / prev * 100.0 if change is not None and prev else None
        key = EastmoneyClient.a_secid(code)
        return key, {
            "secid": key, "code": code, "name": fields[0], "price": price,
            "pct_change": pct, "change": change, "volume": _num(fields[8]),
            "amount": _num(fields[9]), "high": _positive_or_none(fields[4]),
            "low": _positive_or_none(fields[5]), "open": _positive_or_none(fields[1]),
            "prev_close": prev, "quote_time": _local_time_to_utc_iso(stamp, fetched_at),
            "fetched_at": fetched_at.isoformat(), "provider": "sina_http",
        }

    def fetch_a(self, codes: Iterable[str], batch_size: int = 50) -> dict[str, dict]:
        symbols = [self._symbol(code) for code in dict.fromkeys(codes)]
        output: dict[str, dict] = {}
        for idx in range(0, len(symbols), max(1, int(batch_size))):
            batch = symbols[idx:idx + batch_size]
            fetched_at = datetime.now(timezone.utc)
            response = self.session.get(self.URL + ",".join(batch), timeout=self.timeout)
            response.raise_for_status()
            response.encoding = "gbk"
            for line in response.text.splitlines():
                parsed = self._parse_line(line, fetched_at)
                if parsed:
                    output[parsed[0]] = parsed[1]
        return output


class EastmoneyRealtimeClient(_HttpSession):
    """Eastmoney quote adapter kept for FX and last-resort stock fallback."""

    # Backward-compatible parser hook used by tests and provider diagnostics.
    _num = staticmethod(_num)

    URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    FIELDS = "f2,f3,f4,f5,f6,f12,f13,f14,f15,f16,f17,f18,f124"

    def __init__(self, timeout: float = 8.0, save_raw: bool = True) -> None:
        super().__init__(timeout=timeout)
        self.save_raw = save_raw
        self.session.headers.update(EastmoneyClient.HEADERS)

    def _save(self, label: str, payload: dict, params: dict) -> None:
        if not self.save_raw:
            return
        folder = SOURCE_DIR / "eastmoney_realtime" / datetime.now().strftime("%Y-%m-%d")
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{label}_{datetime.now().strftime('%H%M%S_%f')}.json.gz"
        with gzip.open(target, "wt", encoding="utf-8") as handle:
            json.dump({"fetched_at": datetime.now(timezone.utc).isoformat(), "params": params, "payload": payload}, handle, ensure_ascii=False)

    @staticmethod
    def _quote_time(value, fetched_at: datetime) -> str:
        try:
            timestamp = int(float(value))
            if timestamp > 1_000_000_000:
                return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            pass
        # f124 is not consistently a usable exchange timestamp for every instrument.
        # Use fetch time only as transport freshness, never as proof of exchange freshness.
        return fetched_at.isoformat()

    def fetch_secids(self, secids: Iterable[str]) -> dict[str, dict]:
        secids = [item for item in dict.fromkeys(secids) if item]
        if not secids:
            return {}
        fetched_at = datetime.now(timezone.utc)
        params = {"fltt": "2", "invt": "2", "fields": self.FIELDS, "secids": ",".join(secids), "ut": "fa5fd1943c7b386f172d6893dbfba10b"}
        response = self.session.get(self.URL, params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        self._save("batch", payload, params)
        rows = (payload.get("data") or {}).get("diff") or []
        result: dict[str, dict] = {}
        for item in rows:
            code = str(item.get("f12") or "").strip()
            market = str(item.get("f13") or "").strip()
            if market == "116" and code.isdigit():
                code = code.zfill(5)
            elif market in {"0", "1"} and code.isdigit():
                code = code.zfill(6)
            secid = f"{market}.{code}"
            price = _positive_or_none(item.get("f2"))
            if price is None:
                continue
            result[secid] = {
                "secid": secid,
                "code": code,
                "name": item.get("f14"),
                "price": price,
                "pct_change": _num(item.get("f3")),
                "change": _num(item.get("f4")),
                "volume": _num(item.get("f5")),
                "amount": _num(item.get("f6")),
                "high": _positive_or_none(item.get("f15")),
                "low": _positive_or_none(item.get("f16")),
                "open": _positive_or_none(item.get("f17")),
                "prev_close": _positive_or_none(item.get("f18")),
                "quote_time": self._quote_time(item.get("f124"), fetched_at),
                "fetched_at": fetched_at.isoformat(),
                "provider": "eastmoney_http",
            }
        return result

    def fetch_fx(self) -> dict | None:
        quote = self.fetch_secids([EastmoneyClient.fx_secid()]).get(EastmoneyClient.fx_secid())
        if quote and quote.get("price") is not None:
            price = float(quote["price"])
            if price > 10:
                price /= 100.0
            # Eastmoney f124 is not a reliable freshness clock for HKD/CNY.
            # Preserve it as source metadata, but use the successful transport fetch
            # timestamp for the FX freshness gate.
            source_quote_time = quote.get("quote_time")
            fetched_at = quote.get("fetched_at") or datetime.now(timezone.utc).isoformat()
            quote = {
                **quote,
                "price": price,
                "source_quote_time": source_quote_time,
                "quote_time": fetched_at,
                "provider": "eastmoney_fx",
            }
        return quote


class ProviderHealthRegistry:
    """Process-wide provider health, latency EWMA and circuit breaker state."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state: dict[str, dict] = {}

    def _entry(self, name: str) -> dict:
        with self._lock:
            return self._state.setdefault(name, {
                "successes": 0,
                "failures": 0,
                "consecutive_failures": 0,
                "ewma_latency_ms": None,
                "last_latency_ms": None,
                "last_error": None,
                "last_success_at": None,
                "last_failure_at": None,
                "circuit_open_until": 0.0,
            })

    def call(self, name: str, fn, settings):
        entry = self._entry(name)
        now_mono = time.monotonic()
        with self._lock:
            open_until = float(entry.get("circuit_open_until") or 0.0)
        if open_until > now_mono:
            raise RuntimeError(f"{name} circuit open for {open_until - now_mono:.1f}s")

        attempts = max(1, int(settings.fast_retry_attempts) + 1)
        last_exc: Exception | None = None
        for attempt in range(attempts):
            started = time.perf_counter()
            try:
                result = fn()
                latency_ms = (time.perf_counter() - started) * 1000.0
                self.record_success(name, latency_ms, settings.ewma_alpha)
                return result
            except Exception as exc:
                latency_ms = (time.perf_counter() - started) * 1000.0
                last_exc = exc
                self.record_failure(name, latency_ms, exc, settings)
                if attempt + 1 >= attempts:
                    break
                with self._lock:
                    if float(self._entry(name).get("circuit_open_until") or 0.0) > time.monotonic():
                        break
                jitter_ms = random.randint(int(settings.retry_jitter_min_ms), int(settings.retry_jitter_max_ms))
                time.sleep(jitter_ms / 1000.0)
        raise last_exc or RuntimeError(f"{name} provider call failed")

    def record_success(self, name: str, latency_ms: float, alpha: float) -> None:
        with self._lock:
            entry = self._entry(name)
            old = entry.get("ewma_latency_ms")
            ewma = latency_ms if old is None else float(alpha) * latency_ms + (1.0 - float(alpha)) * float(old)
            entry.update({
                "successes": int(entry.get("successes") or 0) + 1,
                "consecutive_failures": 0,
                "ewma_latency_ms": round(ewma, 2),
                "last_latency_ms": round(latency_ms, 2),
                "last_error": None,
                "last_success_at": datetime.now(timezone.utc).isoformat(),
                "circuit_open_until": 0.0,
            })

    def record_failure(self, name: str, latency_ms: float, exc: Exception, settings) -> None:
        with self._lock:
            entry = self._entry(name)
            failures = int(entry.get("consecutive_failures") or 0) + 1
            entry.update({
                "failures": int(entry.get("failures") or 0) + 1,
                "consecutive_failures": failures,
                "last_latency_ms": round(latency_ms, 2),
                "last_error": str(exc),
                "last_failure_at": datetime.now(timezone.utc).isoformat(),
            })
            if failures >= int(settings.circuit_failure_threshold):
                entry["circuit_open_until"] = time.monotonic() + int(settings.circuit_cooldown_seconds)

    def snapshot(self) -> dict[str, dict]:
        now_mono = time.monotonic()
        with self._lock:
            out: dict[str, dict] = {}
            for name, entry in self._state.items():
                open_until = float(entry.get("circuit_open_until") or 0.0)
                remaining = max(0.0, open_until - now_mono)
                out[name] = {
                    "successes": int(entry.get("successes") or 0),
                    "failures": int(entry.get("failures") or 0),
                    "consecutive_failures": int(entry.get("consecutive_failures") or 0),
                    "ewma_latency_ms": entry.get("ewma_latency_ms"),
                    "last_latency_ms": entry.get("last_latency_ms"),
                    "last_error": entry.get("last_error"),
                    "last_success_at": entry.get("last_success_at"),
                    "last_failure_at": entry.get("last_failure_at"),
                    "circuit_state": "open" if remaining > 0 else "closed",
                    "cooldown_remaining_seconds": round(remaining, 1),
                }
            return out


PROVIDER_HEALTH = ProviderHealthRegistry()


class HybridRealtimeClient:
    """Provider router for robust intraday A/H monitoring.

    A shares: Tencent HTTP -> Sina HTTP fallback.
    H shares: Tencent HTTP primary.
    FX: Eastmoney spot (30-second cache).

    Stock legs never fall back to an unverified daily/HTTP timestamp source. If a
    live leg cannot be verified, the premium calculation is paused.

    The router exposes per-leg provider names so the live monitor can enforce strict
    freshness and never mix an old daily cache with a current quote.
    """

    def __init__(self, timeout: float | None = None) -> None:
        self.provider_settings = load_provider_settings()
        timeout = float(timeout if timeout is not None else self.provider_settings.request_timeout_seconds)
        # Separate persistent Tencent sessions let A and H legs be fetched in
        # parallel without sharing a requests.Session across threads.
        self.tencent_a = TencentRealtimeClient(timeout=timeout)
        self.tencent_h = TencentRealtimeClient(timeout=timeout)
        self.tencent = self.tencent_a  # backward-compatible diagnostics/tests
        self.sina = SinaARealtimeClient(timeout=timeout)
        self.eastmoney = EastmoneyRealtimeClient(timeout=timeout)
        self.last_status: dict = {}
        self._fx_cache: dict | None = None
        self._fx_cache_at: datetime | None = None

    def _provider_call(self, name: str, fn):
        return PROVIDER_HEALTH.call(name, fn, self.provider_settings)

    def provider_health(self) -> dict[str, dict]:
        return PROVIDER_HEALTH.snapshot()

    def fetch_pair_quotes(
        self,
        pairs: pd.DataFrame,
        update_a: bool,
        update_h: bool,
        update_fx: bool,
        batch_size: int = 80,
    ) -> tuple[dict[str, dict], dict | None]:
        """Fetch A, H and FX with pooled persistent sessions and bounded parallelism.

        A/H primary requests run concurrently through separate Tencent sessions. FX is
        also fetched in the same bounded worker pool when its global cache expires.
        A-share Sina fallback is requested only for symbols missing from Tencent.
        """
        request_started = time.perf_counter()
        quotes: dict[str, dict] = {}
        errors: dict[str, str] = {}
        a_source = "off"
        h_source = "off"
        fx_source = "off"
        a_codes = pairs["a_code"].astype(str).tolist()
        h_codes = pairs["h_code"].astype(str).tolist()

        fx = self._fx_cache
        need_fx = bool(
            update_fx and (
                self._fx_cache_at is None
                or (datetime.now(timezone.utc) - self._fx_cache_at).total_seconds() >= self.provider_settings.fx_cache_seconds
            )
        )

        # Keep compatibility with injected test/diagnostic clients that replace
        # ``self.tencent`` and may expose the older fetch_a(codes) signature.
        tencent_a_client = self.tencent if self.tencent is not self.tencent_a else self.tencent_a
        tencent_h_client = self.tencent if self.tencent is not self.tencent_a else self.tencent_h

        def fetch_compat(client, method: str, codes: list[str]):
            fn = getattr(client, method)
            try:
                return fn(codes, batch_size=batch_size)
            except TypeError:
                return fn(codes)

        futures = {}
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="ah-quotes") as pool:
            if update_a:
                futures["a"] = pool.submit(self._provider_call, "tencent_a", lambda: fetch_compat(tencent_a_client, "fetch_a", a_codes))
            if update_h:
                futures["h"] = pool.submit(self._provider_call, "tencent_h", lambda: fetch_compat(tencent_h_client, "fetch_h", h_codes))
            if need_fx:
                futures["fx"] = pool.submit(self._provider_call, "eastmoney_fx", self.eastmoney.fetch_fx)

            if "a" in futures:
                try:
                    a_quotes = futures["a"].result()
                    quotes.update(a_quotes)
                    a_source = "tencent_http" if a_quotes else "unavailable"
                except Exception as exc:
                    errors["a_tencent"] = str(exc)
                    a_quotes = {}
                    a_source = "unavailable"
            else:
                a_quotes = {}

            if "h" in futures:
                try:
                    h_quotes = futures["h"].result()
                    quotes.update(h_quotes)
                    h_source = "tencent_http" if h_quotes else "unavailable"
                except Exception as exc:
                    errors["h_tencent"] = str(exc)
                    h_quotes = {}
                    h_source = "unavailable"

            if "fx" in futures:
                try:
                    fresh_fx = futures["fx"].result()
                    if fresh_fx:
                        self._fx_cache = fresh_fx
                        self._fx_cache_at = datetime.now(timezone.utc)
                        fx = fresh_fx
                except Exception as exc:
                    errors["fx_eastmoney"] = str(exc)

        if update_a:
            missing_a = [code for code in a_codes if EastmoneyClient.a_secid(code) not in quotes]
            if missing_a:
                try:
                    fallback = self._provider_call("sina_a", lambda: self.sina.fetch_a(missing_a))
                    quotes.update(fallback)
                    if fallback:
                        a_source = "sina_http" if a_source in {"unavailable", "off"} else "tencent+sina"
                except Exception as exc:
                    errors["a_sina"] = str(exc)

        if update_fx and fx:
            fx_source = str(fx.get("provider") or "eastmoney_fx")

        self.last_status = {
            "a_source": a_source,
            "h_source": h_source,
            "fx_source": fx_source,
            "errors": errors,
            "request_ms": round((time.perf_counter() - request_started) * 1000.0, 2),
            "health": self.provider_health(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        return quotes, fx
