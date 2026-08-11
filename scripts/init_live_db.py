from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.storage.live_store import LiveStore

store = LiveStore()
print(f"Live database ready: {store.path}")
