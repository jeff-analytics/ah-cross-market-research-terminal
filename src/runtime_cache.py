from __future__ import annotations

import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Iterable

import pandas as pd


class MemoryQuoteCache:
    """Thread-safe in-process latest-quote cache.

    The live monitor owns one instance for its whole lifetime. Quote construction reads
    previous values from memory and only persistence is delegated to SQLite. This keeps
    the market-data hot path independent from SQLite latency.
    """

    def __init__(self, initial: Iterable[dict] | pd.DataFrame | None = None) -> None:
        self._lock = threading.RLock()
        self._rows: dict[str, dict] = {}
        self._revision = 0
        self._updated_at: str | None = None
        if initial is not None:
            self.replace(initial)

    @staticmethod
    def _records(value: Iterable[dict] | pd.DataFrame) -> list[dict]:
        if isinstance(value, pd.DataFrame):
            return value.to_dict(orient="records")
        return [dict(item) for item in value]

    def replace(self, value: Iterable[dict] | pd.DataFrame) -> int:
        rows = self._records(value)
        with self._lock:
            self._rows = {
                str(row.get("company_id")): dict(row)
                for row in rows
                if row.get("company_id") is not None
            }
            self._revision += 1
            self._updated_at = datetime.now(timezone.utc).isoformat()
            return self._revision

    def update(self, records: Iterable[dict]) -> int:
        changed = False
        with self._lock:
            for record in records:
                company_id = record.get("company_id")
                if company_id is None:
                    continue
                self._rows[str(company_id)] = dict(record)
                changed = True
            if changed:
                self._revision += 1
                self._updated_at = datetime.now(timezone.utc).isoformat()
            return self._revision

    def latest_map(self, company_ids: Iterable[str] | None = None) -> dict[str, dict]:
        with self._lock:
            if company_ids is None:
                selected = self._rows.items()
            else:
                ids = list(dict.fromkeys(str(x) for x in company_ids))
                selected = ((company_id, self._rows.get(company_id)) for company_id in ids)
            return {
                company_id: deepcopy(row)
                for company_id, row in selected
                if row is not None
            }

    def get(self, company_id: str) -> dict | None:
        with self._lock:
            row = self._rows.get(str(company_id))
            return deepcopy(row) if row is not None else None

    def read_latest(self) -> pd.DataFrame:
        with self._lock:
            rows = [deepcopy(row) for row in self._rows.values()]
        if not rows:
            return pd.DataFrame()
        frame = pd.DataFrame(rows)
        if "company_name" in frame.columns:
            frame = frame.sort_values("company_name")
        return frame.reset_index(drop=True)

    def meta(self) -> dict:
        with self._lock:
            return {
                "rows": len(self._rows),
                "revision": self._revision,
                "updated_at": self._updated_at,
            }


class ServerLiveCache:
    """Read-through server cache synchronized by SQLite's lightweight revision key.

    The browser/API process and the crawler are separate OS processes. The crawler keeps
    the authoritative hot state in its own memory and persists asynchronously. This
    server-side cache therefore checks only a tiny revision value most of the time and
    reloads the 202-row latest table only after the crawler publishes a new revision.
    """

    def __init__(self, store, min_check_interval: float = 0.20) -> None:
        self.store = store
        self.cache = MemoryQuoteCache()
        self.min_check_interval = max(0.05, float(min_check_interval))
        self._last_check = 0.0
        self._db_revision: str | None = None
        self._lock = threading.RLock()
        self.force_reload()

    def force_reload(self) -> None:
        try:
            frame = self.store.read_latest()
            revision = self.store.get_revision()
        except Exception:
            return
        with self._lock:
            self.cache.replace(frame)
            self._db_revision = revision
            self._last_check = time.monotonic()

    def refresh_if_changed(self, force: bool = False) -> bool:
        now = time.monotonic()
        with self._lock:
            if not force and now - self._last_check < self.min_check_interval:
                return False
            self._last_check = now
        try:
            revision = self.store.get_revision()
        except Exception:
            return False
        with self._lock:
            unchanged = revision == self._db_revision
        if unchanged:
            return False
        try:
            frame = self.store.read_latest()
        except Exception:
            return False
        with self._lock:
            self.cache.replace(frame)
            self._db_revision = revision
        return True

    def read_latest(self) -> pd.DataFrame:
        self.refresh_if_changed()
        return self.cache.read_latest()

    def latest_map(self, company_ids: Iterable[str] | None = None) -> dict[str, dict]:
        self.refresh_if_changed()
        return self.cache.latest_map(company_ids)

    def get(self, company_id: str) -> dict | None:
        self.refresh_if_changed()
        return self.cache.get(company_id)

    def meta(self) -> dict:
        data = self.cache.meta()
        data["db_revision"] = self._db_revision
        return data
