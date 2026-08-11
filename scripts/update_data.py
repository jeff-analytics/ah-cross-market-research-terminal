from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import argparse
import json

from src.data.loader import update_from_eastmoney


def main() -> None:
    parser = argparse.ArgumentParser(description="Update full A/H daily data from Eastmoney only")
    parser.add_argument("--start", default=None, help="Optional YYYY-MM-DD. Default is derived from the configured history lookback.")
    parser.add_argument("--end", default=None)
    parser.add_argument("--company", action="append", dest="companies")
    parser.add_argument("--throttle", type=float, default=0.35)
    args = parser.parse_args()
    result = update_from_eastmoney(args.start, args.end, args.companies, args.throttle)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["updated_pairs"] == 0:
        raise SystemExit("No pairs were updated. Existing local data was preserved.")


if __name__ == "__main__":
    main()
