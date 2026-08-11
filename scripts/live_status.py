from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.live_view import load_live_snapshot

latest, status, state = load_live_snapshot()
print(json.dumps({
    "market_state": state.code,
    "market_label": state.label,
    "live_companies": int(len(latest)),
    "runtime_status": status,
}, ensure_ascii=False, indent=2, default=str))
