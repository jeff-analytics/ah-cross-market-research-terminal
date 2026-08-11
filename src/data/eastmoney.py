from __future__ import annotations

import gzip
import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.config import SOURCE_DIR


@dataclass(frozen=True)
class KlineRequest:
    secid: str
    start: str
    end: str
    market: str


class EastmoneyClient:
    """Direct Eastmoney adapter for unadjusted daily A/H/FX history.

    The adapter deliberately bypasses stale CDN responses by adding a cache-buster,
    uses a future network end-date and filters locally to the requested completed
    session, and retries the alternate Eastmoney history host when needed.
    """

    A_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    H_URL = "https://33.push2his.eastmoney.com/api/qt/stock/kline/get"
    NETWORK_END = "20500101"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Referer": "https://quote.eastmoney.com/",
        "Accept": "application/json,text/plain,*/*",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    def __init__(self, timeout: float = 20.0, save_raw: bool = True) -> None:
        self.timeout = timeout
        self.save_raw = save_raw
        self.session = requests.Session()
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.8,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=16))
        self.session.headers.update(self.HEADERS)

    @staticmethod
    def a_secid(code: str) -> str:
        market = "1" if str(code).startswith(("5", "6", "9")) else "0"
        return f"{market}.{str(code).zfill(6)}"

    @staticmethod
    def h_secid(code: str) -> str:
        return f"116.{str(code).zfill(5)}"

    @staticmethod
    def fx_secid() -> str:
        return "120.HKDCNYC"

    def _save_raw(self, label: str, payload: dict, params: dict) -> None:
        if not self.save_raw:
            return
        folder = SOURCE_DIR / "eastmoney" / datetime.now().strftime("%Y-%m-%d")
        folder.mkdir(parents=True, exist_ok=True)
        safe = label.replace(".", "_").replace("/", "_")
        target = folder / f"{safe}_{datetime.now().strftime('%H%M%S_%f')}.json.gz"
        envelope = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "params": params,
            "payload": payload,
        }
        with gzip.open(target, "wt", encoding="utf-8") as handle:
            json.dump(envelope, handle, ensure_ascii=False)

    def _urls_for_market(self, market: str) -> list[str]:
        preferred = self.H_URL if market == "H" else self.A_URL
        alternate = self.A_URL if preferred == self.H_URL else self.H_URL
        return [preferred, alternate]

    @staticmethod
    def _normalize_bound(value: str) -> str:
        text = str(value or "").replace("-", "")
        return text if text else "0"

    def fetch_kline(self, secid: str, start: str, end: str, market: str) -> pd.DataFrame:
        requested_start = self._normalize_bound(start)
        requested_end = self._normalize_bound(end)
        # A future endpoint bound avoids stale responses that can occur when a CDN
        # caches a request ending on the latest completed session. We always filter
        # the returned rows back to requested_end locally, so an intraday partial bar
        # can never leak into a completed-daily chart.
        network_end = self.NETWORK_END
        last_error: Exception | None = None
        best = pd.DataFrame()

        for attempt, url in enumerate(self._urls_for_market(market), start=1):
            params = {
                "secid": secid,
                "ut": "fa5fd1943c7b386f172d6893dbfba10b",
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "klt": "101",
                "fqt": "0",  # unadjusted close; required for actual A/H price comparison
                "beg": requested_start,
                "end": network_end,
                "rtntype": "6",
                "_": str(int(time.time() * 1000) + attempt),
            }
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                response.raise_for_status()
                payload = response.json()
                self._save_raw(f"{secid}_{attempt}", payload, params)
                data = payload.get("data") or {}
                klines = data.get("klines") or []
                if not klines:
                    raise RuntimeError(f"No Eastmoney kline data for {secid} via {url}")
                rows = [line.split(",") for line in klines]
                columns = ["date", "open", "close", "high", "low", "volume", "amount", "amplitude", "pct_change", "change", "turnover"]
                frame = pd.DataFrame(rows, columns=columns)
                frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
                for column in columns[1:]:
                    frame[column] = pd.to_numeric(frame[column], errors="coerce")
                frame = frame.dropna(subset=["date", "close"]).sort_values("date")
                if requested_end.isdigit() and len(requested_end) == 8:
                    frame = frame[frame["date"] <= pd.Timestamp(requested_end)].copy()
                if requested_start not in {"", "0"} and requested_start.isdigit() and len(requested_start) == 8:
                    frame = frame[frame["date"] >= pd.Timestamp(requested_start)].copy()
                if frame.empty:
                    raise RuntimeError(f"Eastmoney returned no rows inside requested range for {secid}")
                if best.empty or frame["date"].max() > best["date"].max():
                    best = frame
                # If the preferred host reaches the requested end (or is only one
                # calendar day behind, which can be a suspension/market holiday), use it.
                if requested_end.isdigit() and len(requested_end) == 8:
                    lag = (pd.Timestamp(requested_end) - frame["date"].max()).days
                    if lag <= 1:
                        return frame
                else:
                    return frame
            except Exception as exc:
                last_error = exc

        if not best.empty:
            return best
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"No Eastmoney kline data for {secid}")

    def fetch_pair(self, a_code: str, h_code: str, start: str, end: str) -> pd.DataFrame:
        def normalize_leg(frame: pd.DataFrame, prefix: str) -> pd.DataFrame:
            frame = frame.copy()
            frame[f"{prefix}_prev_close"] = frame["close"] - frame["change"]
            frame = frame.rename(columns={
                "open": f"{prefix}_open",
                "close": f"{prefix}_close",
                "high": f"{prefix}_high",
                "low": f"{prefix}_low",
                "volume": f"{prefix}_volume",
                "amount": f"{prefix}_amount",
                "pct_change": f"{prefix}_pct_change",
                "change": f"{prefix}_change",
            })
            return frame[[
                "date", f"{prefix}_open", f"{prefix}_close", f"{prefix}_high", f"{prefix}_low",
                f"{prefix}_prev_close", f"{prefix}_pct_change", f"{prefix}_change",
                f"{prefix}_volume", f"{prefix}_amount",
            ]]

        a = normalize_leg(self.fetch_kline(self.a_secid(a_code), start, end, "A"), "a")
        time.sleep(random.uniform(0.08, 0.18))
        h = normalize_leg(self.fetch_kline(self.h_secid(h_code), start, end, "H"), "h")
        return a.merge(h, on="date", how="inner").sort_values("date")

    def fetch_fx(self, start: str, end: str) -> pd.DataFrame:
        fx = self.fetch_kline(self.fx_secid(), start, end, "FX")[["date", "close"]].rename(columns={"close": "fx_cnh_per_hkd"})
        median = fx["fx_cnh_per_hkd"].median()
        # Eastmoney may quote HKDCNY as CNY per 100 HKD. Normalize to CNY per 1 HKD.
        if pd.notna(median) and median > 10:
            fx["fx_cnh_per_hkd"] = fx["fx_cnh_per_hkd"] / 100.0
        median = fx["fx_cnh_per_hkd"].median()
        if pd.isna(median) or median < 0.5 or median > 1.5:
            raise RuntimeError("Eastmoney HKD/CNY unit validation failed")
        fx["fx_source"] = "eastmoney_fx_daily"
        return fx.sort_values("date")
