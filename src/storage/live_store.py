from __future__ import annotations

import json
import queue
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from src.config import LIVE_DB_FILE

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS realtime_latest (
    company_id TEXT PRIMARY KEY,
    company_name TEXT NOT NULL,
    a_code TEXT NOT NULL,
    h_code TEXT NOT NULL,
    industry TEXT,
    fetched_at TEXT NOT NULL,
    a_quote_time TEXT,
    h_quote_time TEXT,
    fx_quote_time TEXT,
    a_price REAL,
    h_price REAL,
    fx_cnh_per_hkd REAL,
    premium_pct REAL,
    premium_change_pp REAL,
    a_contribution_pp REAL,
    h_contribution_pp REAL,
    fx_contribution_pp REAL,
    a_volume REAL,
    h_volume REAL,
    a_amount REAL,
    h_amount REAL,
    a_pct_change REAL,
    h_pct_change REAL,
    a_change REAL,
    h_change REAL,
    a_prev_close REAL,
    h_prev_close REAL,
    a_open REAL,
    h_open REAL,
    a_high REAL,
    a_low REAL,
    h_high REAL,
    h_low REAL,
    market_state TEXT,
    source TEXT,
    data_age_seconds REAL,
    stale_flag INTEGER NOT NULL DEFAULT 0,
    updated_queue TEXT,
    a_source TEXT,
    h_source TEXT,
    fx_source TEXT,
    quote_skew_seconds REAL,
    quality_state TEXT,
    quality_reason TEXT,
    premium_mode TEXT,
    a_session TEXT,
    h_session TEXT,
    sync_premium_pct REAL,
    sync_snapshot_time TEXT
);
CREATE TABLE IF NOT EXISTS intraday_snapshots (
    company_id TEXT NOT NULL,
    snapshot_time TEXT NOT NULL,
    snapshot_bucket TEXT NOT NULL,
    a_price REAL,
    h_price REAL,
    fx_cnh_per_hkd REAL,
    premium_pct REAL,
    premium_change_pp REAL,
    market_state TEXT,
    trigger_reason TEXT,
    source TEXT,
    PRIMARY KEY (company_id, snapshot_bucket)
);
CREATE INDEX IF NOT EXISTS idx_intraday_company_time ON intraday_snapshots(company_id, snapshot_time);
CREATE TABLE IF NOT EXISTS live_alert_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    direction TEXT,
    level INTEGER NOT NULL,
    premium_pct REAL,
    premium_change_pp REAL,
    message TEXT,
    suppressed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_alert_company_time ON live_alert_events(company_id, created_at);
CREATE TABLE IF NOT EXISTS runtime_status (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

LATEST_COLUMNS = [
    "company_id", "company_name", "a_code", "h_code", "industry", "fetched_at",
    "a_quote_time", "h_quote_time", "fx_quote_time", "a_price", "h_price",
    "fx_cnh_per_hkd", "premium_pct", "premium_change_pp", "a_contribution_pp",
    "h_contribution_pp", "fx_contribution_pp", "a_volume", "h_volume", "a_amount",
    "h_amount", "a_pct_change", "h_pct_change", "a_change", "h_change",
    "a_prev_close", "h_prev_close", "a_open", "h_open", "a_high", "a_low", "h_high", "h_low",
    "market_state", "source", "data_age_seconds", "stale_flag", "updated_queue",
    "a_source", "h_source", "fx_source", "quote_skew_seconds", "quality_state", "quality_reason",
    "premium_mode", "a_session", "h_session", "sync_premium_pct", "sync_snapshot_time",
]
SNAPSHOT_COLUMNS = [
    "company_id", "snapshot_time", "snapshot_bucket", "a_price", "h_price",
    "fx_cnh_per_hkd", "premium_pct", "premium_change_pp", "market_state",
    "trigger_reason", "source",
]


def _status_payload(value: object) -> str:
    return json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value


class LiveStore:
    def __init__(self, path: Path = LIVE_DB_FILE) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(realtime_latest)").fetchall()}
            additions = {
                "a_pct_change": "REAL", "h_pct_change": "REAL",
                "a_change": "REAL", "h_change": "REAL",
                "a_prev_close": "REAL", "h_prev_close": "REAL",
                "a_open": "REAL", "h_open": "REAL",
                "a_high": "REAL", "a_low": "REAL", "h_high": "REAL", "h_low": "REAL",
                "a_source": "TEXT", "h_source": "TEXT", "fx_source": "TEXT",
                "quote_skew_seconds": "REAL", "quality_state": "TEXT", "quality_reason": "TEXT",
                "premium_mode": "TEXT", "a_session": "TEXT", "h_session": "TEXT",
                "sync_premium_pct": "REAL", "sync_snapshot_time": "TEXT",
            }
            for name, sql_type in additions.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE realtime_latest ADD COLUMN {name} {sql_type}")
            # The revision key is intentionally tiny. The web process can poll it
            # cheaply and only reload the full latest table when it changes.
            row = conn.execute("SELECT value FROM runtime_status WHERE key='memory_revision'").fetchone()
            if row is None:
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "INSERT INTO runtime_status(key,value,updated_at) VALUES(?,?,?)",
                    ("memory_revision", "0", now),
                )

    @staticmethod
    def _upsert_latest_conn(conn: sqlite3.Connection, records: list[dict]) -> None:
        if not records:
            return
        sql = f"""
        INSERT INTO realtime_latest ({','.join(LATEST_COLUMNS)}) VALUES ({','.join('?' for _ in LATEST_COLUMNS)})
        ON CONFLICT(company_id) DO UPDATE SET
        {','.join(f'{column}=excluded.{column}' for column in LATEST_COLUMNS if column != 'company_id')}
        """
        values = [[record.get(column) for column in LATEST_COLUMNS] for record in records]
        conn.executemany(sql, values)

    @staticmethod
    def _insert_snapshots_conn(conn: sqlite3.Connection, records: list[dict]) -> None:
        if not records:
            return
        sql = f"INSERT OR REPLACE INTO intraday_snapshots ({','.join(SNAPSHOT_COLUMNS)}) VALUES ({','.join('?' for _ in SNAPSHOT_COLUMNS)})"
        values = [[record.get(column) for column in SNAPSHOT_COLUMNS] for record in records]
        conn.executemany(sql, values)

    @staticmethod
    def _set_status_conn(conn: sqlite3.Connection, key: str, value: object) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO runtime_status(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, _status_payload(value), now),
        )

    @staticmethod
    def _bump_revision_conn(conn: sqlite3.Connection) -> str:
        revision = str(time.time_ns())
        LiveStore._set_status_conn(conn, "memory_revision", revision)
        return revision

    def latest_map(self, company_ids: Iterable[str] | None = None) -> dict[str, dict]:
        with self.connect() as conn:
            if company_ids:
                ids = list(dict.fromkeys(str(x) for x in company_ids))
                placeholders = ",".join("?" for _ in ids)
                rows = conn.execute(f"SELECT * FROM realtime_latest WHERE company_id IN ({placeholders})", ids).fetchall()
            else:
                rows = conn.execute("SELECT * FROM realtime_latest").fetchall()
        return {str(row["company_id"]): dict(row) for row in rows}

    def read_latest(self) -> pd.DataFrame:
        with self.connect() as conn:
            return pd.read_sql_query("SELECT * FROM realtime_latest ORDER BY company_name", conn)

    def read_snapshots(self, company_id: str, limit: int = 500) -> pd.DataFrame:
        with self.connect() as conn:
            return pd.read_sql_query(
                "SELECT * FROM intraday_snapshots WHERE company_id=? ORDER BY snapshot_time DESC LIMIT ?",
                conn,
                params=(company_id, int(limit)),
            ).sort_values("snapshot_time")

    def upsert_latest(self, records: list[dict]) -> None:
        if not records:
            return
        with self.connect() as conn:
            self._upsert_latest_conn(conn, records)
            self._bump_revision_conn(conn)

    def insert_snapshots(self, records: list[dict]) -> None:
        if not records:
            return
        with self.connect() as conn:
            self._insert_snapshots_conn(conn, records)

    def write_batch(self, latest: list[dict] | None = None, snapshots: list[dict] | None = None, statuses: dict[str, object] | None = None) -> str | None:
        latest = latest or []
        snapshots = snapshots or []
        statuses = statuses or {}
        if not latest and not snapshots and not statuses:
            return None
        with self.connect() as conn:
            self._upsert_latest_conn(conn, latest)
            self._insert_snapshots_conn(conn, snapshots)
            for key, value in statuses.items():
                self._set_status_conn(conn, key, value)
            return self._bump_revision_conn(conn) if latest else None

    def set_status(self, key: str, value: object) -> None:
        with self.connect() as conn:
            self._set_status_conn(conn, key, value)

    def get_status(self) -> dict[str, dict[str, str]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT key,value,updated_at FROM runtime_status").fetchall()
        return {str(row["key"]): {"value": str(row["value"]), "updated_at": str(row["updated_at"])} for row in rows}

    def get_revision(self) -> str:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM runtime_status WHERE key='memory_revision'").fetchone()
        return str(row["value"]) if row else "0"

    def record_alert(
        self,
        company_id: str,
        alert_type: str,
        direction: str,
        level: int,
        premium_pct: float | None,
        premium_change_pp: float | None,
        message: str,
        suppressed: bool,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO live_alert_events(company_id,created_at,alert_type,direction,level,premium_pct,premium_change_pp,message,suppressed) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    company_id,
                    datetime.now(timezone.utc).isoformat(),
                    alert_type,
                    direction,
                    int(level),
                    premium_pct,
                    premium_change_pp,
                    message,
                    1 if suppressed else 0,
                ),
            )

    def last_alert(self, company_id: str, alert_type: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM live_alert_events WHERE company_id=? AND alert_type=? AND suppressed=0 ORDER BY created_at DESC LIMIT 1",
                (company_id, alert_type),
            ).fetchone()
        return dict(row) if row else None


class AsyncLiveWriter:
    """Single SQLite writer thread with coalescing.

    Network fetch threads never wait for SQLite commits in normal monitor operation.
    Latest rows are coalesced by company, snapshot rows by time bucket, and status
    updates by key before one transaction is committed.
    """

    def __init__(self, store: LiveStore, max_queue: int = 4096, flush_interval: float = 0.08) -> None:
        self.store = store
        self.queue: queue.Queue = queue.Queue(maxsize=max(128, int(max_queue)))
        self.flush_interval = max(0.01, float(flush_interval))
        self._stop = threading.Event()
        self._active = threading.Event()
        self._stats_lock = threading.Lock()
        self._written_batches = 0
        self._written_latest = 0
        self._written_snapshots = 0
        self._fallback_sync = 0
        self._last_error: str | None = None
        self.thread = threading.Thread(target=self._worker, name="ah-sqlite-writer", daemon=True)
        self.thread.start()

    def _submit(self, kind: str, payload: object) -> None:
        if payload in (None, [], {}):
            return
        try:
            self.queue.put_nowait((kind, payload))
        except queue.Full:
            # Preserve correctness under an extreme backlog. This rare path can block,
            # but normal operation remains fully asynchronous.
            with self._stats_lock:
                self._fallback_sync += 1
            if kind == "latest":
                self.store.upsert_latest(list(payload))
            elif kind == "snapshots":
                self.store.insert_snapshots(list(payload))
            elif kind == "status":
                key, value = payload
                self.store.set_status(str(key), value)

    def submit_latest(self, records: list[dict]) -> None:
        self._submit("latest", records)

    def submit_snapshots(self, records: list[dict]) -> None:
        self._submit("snapshots", records)

    def submit_status(self, key: str, value: object) -> None:
        self._submit("status", (key, value))

    def _worker(self) -> None:
        while not self._stop.is_set() or not self.queue.empty():
            try:
                first = self.queue.get(timeout=0.1)
            except queue.Empty:
                continue
            ops = [first]
            deadline = time.monotonic() + self.flush_interval
            while time.monotonic() < deadline and len(ops) < 256:
                try:
                    ops.append(self.queue.get_nowait())
                except queue.Empty:
                    break
            latest_map: dict[str, dict] = {}
            snapshot_map: dict[tuple[str, str], dict] = {}
            statuses: dict[str, object] = {}
            for kind, payload in ops:
                if kind == "latest":
                    for record in payload:
                        company_id = record.get("company_id")
                        if company_id is not None:
                            latest_map[str(company_id)] = dict(record)
                elif kind == "snapshots":
                    for record in payload:
                        key = (str(record.get("company_id")), str(record.get("snapshot_bucket")))
                        snapshot_map[key] = dict(record)
                elif kind == "status":
                    key, value = payload
                    statuses[str(key)] = value
            self._active.set()
            try:
                self.store.write_batch(list(latest_map.values()), list(snapshot_map.values()), statuses)
                with self._stats_lock:
                    self._written_batches += 1
                    self._written_latest += len(latest_map)
                    self._written_snapshots += len(snapshot_map)
                    self._last_error = None
            except Exception as exc:
                with self._stats_lock:
                    self._last_error = str(exc)
            finally:
                self._active.clear()
                for _ in ops:
                    self.queue.task_done()

    def flush(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() <= deadline:
            if self.queue.unfinished_tasks == 0 and not self._active.is_set():
                return True
            time.sleep(0.01)
        return False

    def metrics(self) -> dict:
        with self._stats_lock:
            return {
                "queue_depth": self.queue.qsize(),
                "written_batches": self._written_batches,
                "written_latest": self._written_latest,
                "written_snapshots": self._written_snapshots,
                "fallback_sync": self._fallback_sync,
                "last_error": self._last_error,
                "thread_alive": self.thread.is_alive(),
            }

    def close(self, timeout: float = 5.0) -> None:
        self.flush(timeout=timeout)
        self._stop.set()
        self.thread.join(timeout=max(0.1, timeout))
