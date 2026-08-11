from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analysis.screener import build_screener
from src.config import RESULTS_FILE
from src.data.loader import load_prices

if __name__ == "__main__":
    screen, _ = build_screener(load_prices())
    screen.to_csv(RESULTS_FILE, index=False, encoding="utf-8-sig")
    print(f"Exported {len(screen)} rows to {RESULTS_FILE}")
