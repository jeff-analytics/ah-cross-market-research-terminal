from __future__ import annotations
import json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from src.data.pairs import universe_status, load_pairs
s=universe_status(); print(json.dumps(s,ensure_ascii=False,indent=2))
p=load_pairs(active_only=True)
print(f"\nActive unique A codes: {p.a_code.nunique()}")
print(f"Active unique H codes: {p.h_code.nunique()}")
print(f"Contains 中际旭创: {bool((p.a_code=='300308').any())}")
