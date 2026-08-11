from __future__ import annotations

from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.loader import update_from_eastmoney
from src.data.pairs import sync_universe_from_eastmoney

if __name__ == "__main__":
    universe = sync_universe_from_eastmoney()
    print("\n[1/2] Universe sync")
    print(json.dumps(universe, ensure_ascii=False, indent=2))
    print("\n[2/2] Eastmoney price update")
    market = update_from_eastmoney()
    print(json.dumps(market, ensure_ascii=False, indent=2))
