from __future__ import annotations

from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.pairs import sync_universe_from_eastmoney

if __name__ == "__main__":
    result = sync_universe_from_eastmoney()
    print(json.dumps(result, ensure_ascii=False, indent=2))
