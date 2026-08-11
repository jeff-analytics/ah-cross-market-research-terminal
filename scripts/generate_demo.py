from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.demo import generate_demo_data

if __name__ == "__main__":
    frame = generate_demo_data(force_registry=True)
    print(
        f"Generated full-universe demonstration data: "
        f"{frame['company_id'].nunique()} active pairs, {len(frame):,} rows."
    )
