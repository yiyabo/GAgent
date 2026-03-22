"""Audit logging for terminal sessions."""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class AuditLogger:
    """Per-terminal SQLite audit logger."""

    def __init__(self, terminal_id: str, *, audit_root: str | Path | None = None) -> None:
        self.terminal_id = str(terminal_id)
        resolved_root = audit_root or os.getenv("TERMINAL_AUDIT_ROOT", "runtime/terminal_audit")
        self.audit_root = Path(resolved_root).expanduser().resolve()
        self.audit_root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.audit_root / f"{self.terminal_id}.sqlite"
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    event_type TEXT NOT NULL,
                    data BLOB,
                    metadata TEXT
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_entries_ts ON audit_entries(timestamp)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_entries_type ON audit_entries(event_type)"
            )
            self._conn.commit()

    def log_event(
        self,
        event_type: str,
        *,
        data: bytes | str | None = None,
        metadata: Optional[Dict[str, Any]] = None,
        timestamp: Optional[float] = None,
    ) -> int:
        event_ts = float(timestamp if timestamp is not None else time.time())
        if data is None:
            payload = None
        elif isinstance(data, bytes):
            payload = data
        else:
            payload = str(data).encode("utf-8", errors="replace")

        metadata_text = json.dumps(metadata or {}, ensure_ascii=False)
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO audit_entries(timestamp, event_type, data, metadata) VALUES (?, ?, ?, ?)",
                (event_ts, str(event_type), payload, metadata_text),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def query_events(
        self,
        *,
        start_ts: Optional[float] = None,
        end_ts: Optional[float] = None,
        event_type: Optional[str] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        sql = "SELECT id, timestamp, event_type, data, metadata FROM audit_entries WHERE 1=1"
        params: List[Any] = []
        if start_ts is not None:
            sql += " AND timestamp >= ?"
            params.append(float(start_ts))
        if end_ts is not None:
            sql += " AND timestamp <= ?"
            params.append(float(end_ts))
        if event_type:
            sql += " AND event_type = ?"
            params.append(str(event_type))
        sql += " ORDER BY timestamp ASC, id ASC LIMIT ?"
        params.append(max(1, int(limit)))

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()

        result: List[Dict[str, Any]] = []
        for row in rows:
            metadata_raw = row["metadata"]
            metadata: Dict[str, Any] = {}
            if isinstance(metadata_raw, str) and metadata_raw.strip():
                try:
                    parsed = json.loads(metadata_raw)
                    if isinstance(parsed, dict):
                        metadata = parsed
                except json.JSONDecodeError:
                    metadata = {"raw_metadata": metadata_raw}
            data_blob = row["data"]
            encoded = ""
            if isinstance(data_blob, (bytes, bytearray)) and data_blob:
                encoded = base64.b64encode(bytes(data_blob)).decode("ascii")
            result.append(
                {
                    "id": int(row["id"]),
                    "timestamp": float(row["timestamp"]),
                    "event_type": str(row["event_type"]),
                    "data": encoded,
                    "metadata": metadata,
                }
            )
        return result

    def build_replay(
        self,
        *,
        limit: int = 4000,
        include_input: bool = True,
    ) -> List[Dict[str, Any]]:
        kinds = ["output"]
        if include_input:
            kinds.append("input")

        placeholders = ",".join("?" for _ in kinds)
        query_limit = max(1, int(limit))

        sql = (
            "SELECT id, timestamp, event_type, data FROM audit_entries "
            f"WHERE event_type IN ({placeholders}) "
            "ORDER BY timestamp DESC, id DESC LIMIT ?"
        )

        with self._lock:
            rows = self._conn.execute(sql, [*kinds, query_limit]).fetchall()

        rows = list(reversed(rows))
        replay: List[Dict[str, Any]] = []
        prev_ts: Optional[float] = None
        for row in rows:
            ts = float(row["timestamp"])
            delay = 0.0 if prev_ts is None else max(0.0, ts - prev_ts)
            prev_ts = ts
            blob = row["data"]
            encoded = ""
            if isinstance(blob, (bytes, bytearray)) and blob:
                encoded = base64.b64encode(bytes(blob)).decode("ascii")
            replay.append(
                {
                    "delay": delay,
                    "type": "o" if str(row["event_type"]) == "output" else "i",
                    "data": encoded,
                }
            )
        return replay

    def prune_older_than(self, *, days: int = 7) -> int:
        cutoff = time.time() - max(0, int(days)) * 24 * 60 * 60
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM audit_entries WHERE timestamp < ?", (cutoff,))
            self._conn.commit()
            return int(cur.rowcount or 0)

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
