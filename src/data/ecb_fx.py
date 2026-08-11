from __future__ import annotations

from io import StringIO

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class EcbFxClient:
    """Official ECB daily reference-rate fallback for historical HKD/CNY.

    ECB publishes CNY/EUR and HKD/EUR reference-rate series.  Their ratio gives
    CNY per HKD.  This fallback is used only for *daily historical* conversion;
    intraday/live FX continues to use the real-time provider stack.
    """

    URL = "https://data-api.ecb.europa.eu/service/data/EXR/D.CNY+HKD.EUR.SP00.A"
    HEADERS = {
        "Accept": "text/csv",
        "User-Agent": "AH-Cross-Market-Research-Terminal/5.1.4",
    }

    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.8,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8))
        self.session.headers.update(self.HEADERS)

    def fetch_fx(self, start: str, end: str) -> pd.DataFrame:
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        params = {
            "startPeriod": start_ts.date().isoformat(),
            "endPeriod": end_ts.date().isoformat(),
            "format": "csvdata",
            "detail": "dataonly",
        }
        response = self.session.get(self.URL, params=params, timeout=self.timeout)
        response.raise_for_status()
        frame = pd.read_csv(StringIO(response.text))
        if frame.empty:
            raise RuntimeError("ECB returned no FX observations")

        # ECB CSV schemas have been stable, but keep the parser tolerant to case.
        lower = {str(c).upper(): c for c in frame.columns}
        date_col = lower.get("TIME_PERIOD")
        value_col = lower.get("OBS_VALUE")
        currency_col = lower.get("CURRENCY")
        if not date_col or not value_col or not currency_col:
            raise RuntimeError(f"Unexpected ECB CSV columns: {list(frame.columns)}")

        frame = frame[[date_col, value_col, currency_col]].copy()
        frame.columns = ["date", "value", "currency"]
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
        frame["currency"] = frame["currency"].astype(str).str.upper()
        frame = frame.dropna(subset=["date", "value"])

        pivot = frame.pivot_table(index="date", columns="currency", values="value", aggfunc="last")
        if "CNY" not in pivot.columns or "HKD" not in pivot.columns:
            raise RuntimeError("ECB response is missing CNY or HKD reference rates")
        out = (pivot["CNY"] / pivot["HKD"]).rename("fx_cnh_per_hkd").reset_index()
        out["fx_source"] = "ecb_reference_cross"
        out = out.dropna(subset=["fx_cnh_per_hkd"]).sort_values("date")
        median = out["fx_cnh_per_hkd"].median()
        if pd.isna(median) or median < 0.5 or median > 1.5:
            raise RuntimeError("ECB HKD/CNY cross-rate validation failed")
        return out
