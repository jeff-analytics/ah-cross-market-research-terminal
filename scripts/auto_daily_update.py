from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.loader import update_from_eastmoney
from src.data.pairs import sync_universe_from_eastmoney

PID_FILE = ROOT / "data" / "daily_crawler.pid"
STATUS_FILE = ROOT / "data" / "daily_crawler_status.json"


def write_status(payload: dict) -> None:
    STATUS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default=None)
    parser.add_argument("--skip-universe", action="store_true")
    parser.add_argument("--throttle", type=float, default=0.35)
    args = parser.parse_args()
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    started = datetime.now(timezone.utc).isoformat()
    write_status({"state": "running", "started_at": started, "source": "eastmoney", "stage": "universe"})
    try:
        universe = {"status": "skipped"}
        if not args.skip_universe:
            universe = sync_universe_from_eastmoney()
        write_status({"state": "running", "started_at": started, "source": "eastmoney", "stage": "daily_prices", "universe": universe})
        market = update_from_eastmoney(end=args.end, throttle_seconds=args.throttle)
        state = "success" if market.get("status") == "success" else ("degraded" if market.get("updated_pairs", 0) else "failed")
        write_status({"state": state, "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(), "source": "eastmoney", "universe": universe, "market": market})
    except Exception as exc:
        write_status({"state": "failed", "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(), "source": "eastmoney", "error": str(exc)})
        raise
    finally:
        PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
