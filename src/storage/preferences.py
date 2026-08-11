from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from src.config import DATA_DIR

WATCHLIST_FILE = DATA_DIR / "watchlist.json"
AUTO_TOP5_MODE = "auto_top5"
CUSTOM_MODE = "custom"


def _read_watchlist_payload() -> dict:
    if not WATCHLIST_FILE.exists():
        return {"mode": AUTO_TOP5_MODE, "company_ids": []}
    try:
        raw = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {"mode": AUTO_TOP5_MODE, "company_ids": []}
    except (OSError, json.JSONDecodeError, TypeError):
        return {"mode": AUTO_TOP5_MODE, "company_ids": []}


def watchlist_mode() -> str:
    raw = _read_watchlist_payload()
    mode = str(raw.get("mode") or "").strip().lower()
    # Old user-created watchlist files did not contain a mode. Preserve them as
    # custom selections instead of silently replacing a user's choices.
    if mode not in {AUTO_TOP5_MODE, CUSTOM_MODE}:
        return CUSTOM_MODE if raw.get("company_ids") else AUTO_TOP5_MODE
    return mode


def load_watchlist(
    available_ids: Iterable[str],
    default_count: int = 5,
    default_ids: Iterable[str] | None = None,
) -> list[str]:
    available = list(dict.fromkeys(str(item) for item in available_ids))
    available_set = set(available)
    raw = _read_watchlist_payload()
    mode = watchlist_mode()

    if mode == CUSTOM_MODE:
        # An intentionally empty custom watchlist must stay empty.
        return [str(item) for item in raw.get("company_ids", []) if str(item) in available_set]

    ranked = [str(item) for item in (default_ids or []) if str(item) in available_set]
    ranked = list(dict.fromkeys(ranked))
    if ranked:
        return ranked[: max(0, int(default_count))]
    return available[: max(0, int(default_count))]


def save_watchlist(company_ids: Iterable[str]) -> Path:
    WATCHLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mode": CUSTOM_MODE,
        "company_ids": list(dict.fromkeys(str(item) for item in company_ids)),
    }
    WATCHLIST_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return WATCHLIST_FILE


def save_auto_watchlist() -> Path:
    WATCHLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mode": AUTO_TOP5_MODE,
        "company_ids": [],
        "selection_basis": "research_priority_top5",
    }
    WATCHLIST_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return WATCHLIST_FILE
