from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.live_monitor import LiveMonitor


def main() -> None:
    parser = argparse.ArgumentParser(description="Run adaptive intraday A/H snapshot monitor")
    parser.add_argument("--once", action="store_true", help="Run one forced full-universe snapshot and exit")
    parser.add_argument("--max-cycles", type=int, default=None)
    parser.add_argument("--sleep", type=float, default=1.0)
    args = parser.parse_args()
    monitor = LiveMonitor()
    if args.once:
        print(json.dumps(monitor.run_once(force=True), ensure_ascii=False, indent=2))
    else:
        monitor.run_forever(sleep_seconds=args.sleep, max_cycles=args.max_cycles)


if __name__ == "__main__":
    main()
