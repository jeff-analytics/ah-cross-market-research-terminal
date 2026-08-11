from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from src.config import FOCUS_STATE_FILE

_lock = threading.RLock()
_cached_mtime = -1.0
_cached_ids: list[str] = []


def save_focus(company_id: str, path: Path = FOCUS_STATE_FILE) -> list[str]:
    company_id = str(company_id or "").strip()
    if not company_id:
        return load_focus_ids(path)
    with _lock:
        current = load_focus_ids(path)
        ids = [company_id] + [x for x in current if x != company_id]
        ids = ids[:4]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"company_ids": ids, "updated_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        global _cached_mtime, _cached_ids
        try:
            _cached_mtime = path.stat().st_mtime
        except OSError:
            _cached_mtime = -1.0
        _cached_ids = ids
        return list(ids)


def load_focus_ids(path: Path = FOCUS_STATE_FILE) -> list[str]:
    global _cached_mtime, _cached_ids
    with _lock:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return list(_cached_ids)
        if mtime == _cached_mtime:
            return list(_cached_ids)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            ids = [str(x) for x in raw.get("company_ids", []) if str(x).strip()]
        except Exception:
            ids = []
        _cached_mtime = mtime
        _cached_ids = list(dict.fromkeys(ids))[:4]
        return list(_cached_ids)
