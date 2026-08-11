from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from src.data.pairs import sync_universe_from_eastmoney
print(json.dumps(sync_universe_from_eastmoney(),ensure_ascii=False,indent=2))
