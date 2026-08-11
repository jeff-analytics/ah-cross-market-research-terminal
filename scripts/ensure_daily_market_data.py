from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.history_sync import market_history_audit, sync_market_recent_history

DATA = ROOT / "data"
STATUS_FILE = DATA / "daily_crawler_status.json"
UPDATE_LOG = DATA / "update_log.json"
TZ = ZoneInfo("Asia/Shanghai")


def target_date(now: datetime) -> date:
    d = now.date() if now.time() >= time(16, 20) else now.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def last_success_date() -> date | None:
    if not UPDATE_LOG.exists():
        return None
    try:
        payload = json.loads(UPDATE_LOG.read_text(encoding="utf-8"))
        if payload.get("status") != "success":
            return None
        requested = int(payload.get("requested_pairs") or 0)
        updated = int(payload.get("updated_pairs") or 0)
        failed = payload.get("failed_pairs") or []
        lagging = payload.get("lagging_pairs") or []
        if requested and (updated < requested or failed or lagging):
            return None
        end = str(payload.get("end") or "")[:8]
        if len(end) == 8 and end.isdigit():
            return datetime.strptime(end, "%Y%m%d").date()
    except Exception:
        return None
    return None


def _write(payload: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    """Guarantee a universe-wide recent daily-history refresh before UI startup.

    v5.1.4 still relied primarily on per-company on-demand refresh while a market-wide
    crawler ran asynchronously.  That meant the user could open many companies and
    still see the bundled 08-05 preview before the background job reached them.

    v5.1.5 audits every active A/H pair first and synchronously refreshes every stale
    company through the latest completed daily-session target.  Per-company sync is
    retained only as a fallback for transient provider failures.
    """
    now = datetime.now(TZ)
    before = market_history_audit()
    print(
        f"Formal daily coverage before startup: {before['current_pairs']}/{before['total_pairs']} "
        f"through {before['expected_through']}."
    )
    if int(before.get("stale_pairs") or 0) == 0:
        _write({"state": "success", "scope": "whole_universe", "checked_at": now.isoformat(), "audit": before})
        print("Daily A/H history is current for the full active universe.")
        return 0

    throttle = float(os.environ.get("AH_STARTUP_DAILY_THROTTLE", "0.08"))
    _write({"state": "running", "scope": "whole_universe", "started_at": now.isoformat(), "audit_before": before})
    try:
        result = sync_market_recent_history(throttle_seconds=throttle)
        after = result["after"]
        state = "success" if result.get("status") == "current" else "degraded"
        _write({
            "state": state,
            "scope": "whole_universe",
            "started_at": now.isoformat(),
            "finished_at": datetime.now(TZ).isoformat(),
            "audit_before": before,
            "audit_after": after,
            "update": result.get("update"),
        })
        print(
            f"Formal daily coverage after startup sync: {after['current_pairs']}/{after['total_pairs']} "
            f"through {after['expected_through']}."
        )
        if int(after.get("stale_pairs") or 0):
            print(
                f"WARNING: {after['stale_pairs']} pair(s) are still behind the latest completed "
                "session. The terminal will label those companies as stale and retry on demand."
            )
        return 0
    except Exception as exc:
        _write({
            "state": "failed",
            "scope": "whole_universe",
            "started_at": now.isoformat(),
            "finished_at": datetime.now(TZ).isoformat(),
            "audit_before": before,
            "error": str(exc),
        })
        print(f"WARNING: whole-universe daily sync failed: {exc}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
