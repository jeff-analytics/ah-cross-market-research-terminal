from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import HISTORY_COVERAGE_FILE
from src.data.history_sync import FULL_HISTORY_BEGIN, latest_completed_daily_date
from src.data.loader import load_prices, update_from_eastmoney
from src.data.pairs import load_pairs

TZ = ZoneInfo("Asia/Shanghai")


def main() -> int:
    target = latest_completed_daily_date().strftime("%Y%m%d")
    pairs = load_pairs(active_only=True)
    print(f"Backfilling complete A/H daily history for {len(pairs)} companies through {target}...")
    print("This is a one-time network-heavy operation and may take several minutes.")
    result = update_from_eastmoney(
        start=FULL_HISTORY_BEGIN,
        end=target,
        throttle_seconds=0.20,
        force_start=True,
    )

    prices = load_prices()
    real = prices[prices.get("data_source", "").astype(str).str.startswith("eastmoney")].copy()
    failed = {str(x.get("company_id")) for x in (result.get("failed_pairs") or [])}
    coverage = {"companies": {}}
    if HISTORY_COVERAGE_FILE.exists():
        try:
            coverage = json.loads(HISTORY_COVERAGE_FILE.read_text(encoding="utf-8"))
        except Exception:
            coverage = {"companies": {}}
    companies = coverage.setdefault("companies", {})
    for cid, group in real.groupby(real["company_id"].astype(str)):
        if cid in failed:
            continue
        dates = group["date"]
        companies[cid] = {
            **companies.get(cid, {}),
            "full_history": True,
            "earliest": str(dates.min().date()),
            "latest": str(dates.max().date()),
            "updated_at": datetime.now(TZ).isoformat(),
            "source": "eastmoney",
        }
    HISTORY_COVERAGE_FILE.write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"success", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
