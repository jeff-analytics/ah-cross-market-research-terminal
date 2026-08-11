from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import SETTINGS, UNIVERSE_LOG_FILE
from src.data.pairs import load_pairs, sync_universe_from_eastmoney, universe_status


def needs_sync() -> bool:
    pairs = load_pairs(active_only=True)
    if len(pairs) < SETTINGS.expected_universe_count:
        return True
    if not UNIVERSE_LOG_FILE.exists():
        return True
    try:
        log = json.loads(UNIVERSE_LOG_FILE.read_text(encoding='utf-8'))
        stamp = datetime.fromisoformat(str(log.get('updated_at') or '').replace('Z','+00:00'))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - stamp > timedelta(hours=SETTINGS.universe_sync_hours)
    except Exception:
        return True


def main() -> int:
    before = universe_status()
    print(f"A/H universe before sync: {before['active_count']}/{before['target_count']} ({before['source']})")
    if needs_sync():
        result = sync_universe_from_eastmoney()
        print(json.dumps(result, ensure_ascii=False))
    after = universe_status()
    print(f"A/H universe ready: {after['active_count']}/{after['target_count']} full_ready={after['full_ready']}")
    return 0 if after['full_ready'] else 2

if __name__ == '__main__':
    raise SystemExit(main())
