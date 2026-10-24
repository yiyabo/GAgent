"""Pipelines, their frozen versions, and runs with one row per node (main database, raw SQL).

A version is immutable once written; a run always points at one, so a finished run can be read back
against exactly the graph it executed. Run status uses the same ``queued``/``running`` words the deploy
gate and session retention look for.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

ACTIVE_RUN_STATUSES = ("queued", "running")
FINISHED_NODE_STATUSES = ("succeeded", "cached")


def ensure_schema(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS pipelines (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            draft TEXT NOT NULL,
            latest_version INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pipelines_owner ON pipelines(owner_id, archived, updated_at)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS pipeline_versions (
            pipeline_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            graph TEXT NOT NULL,
            graph_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (pipeline_id, version)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS pipeline_runs (
            id TEXT PRIMARY KEY,
            pipeline_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            owner_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            status TEXT NOT NULL,
            inputs TEXT,
            error TEXT,
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            started_at TEXT,
            finished_at TEXT
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_runs_pipeline ON pipeline_runs(pipeline_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status ON pipeline_runs(status)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS pipeline_node_runs (
            run_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            status TEXT NOT NULL,
            cache_key TEXT,
            outputs TEXT,
            detail TEXT,
            error TEXT,
            started_at TEXT,
            finished_at TEXT,
            PRIMARY KEY (run_id, node_id)
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_node_runs_cache ON pipeline_node_runs(cache_key, status)")


def _loads(text: Any, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _pipeline_row(row: Any) -> Dict[str, Any]:
    item = dict(row)
    item["draft"] = _loads(item.get("draft"), {})
    item["archived"] = bool(item.get("archived"))
    return item


# ------------------------------------------------------------------ pipelines
def create_pipeline(conn, owner_id: str, name: str, description: str, draft: Dict[str, Any]) -> str:
    pipeline_id = "pl_" + uuid.uuid4().hex[:16]
    conn.execute(
        "INSERT INTO pipelines (id, owner_id, name, description, draft) VALUES (?, ?, ?, ?, ?)",
        (pipeline_id, owner_id, name, description, _dumps(draft)),
    )
    return pipeline_id


def get_pipeline(conn, pipeline_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM pipelines WHERE id=? AND archived=0", (pipeline_id,)).fetchone()
    return _pipeline_row(row) if row else None


def list_pipelines(conn, owner_id: str) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """SELECT p.id, p.name, p.description, p.latest_version, p.created_at, p.updated_at,
                  (SELECT r.status FROM pipeline_runs r WHERE r.pipeline_id = p.id
                   ORDER BY r.created_at DESC, r.rowid DESC LIMIT 1) AS last_run_status
           FROM pipelines p WHERE p.owner_id=? AND p.archived=0 ORDER BY p.updated_at DESC, p.rowid DESC""",
        (owner_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def update_pipeline(
    conn,
    pipeline_id: str,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    draft: Optional[Dict[str, Any]] = None,
) -> None:
    fields: List[str] = []
    values: List[Any] = []
    if name is not None:
        fields.append("name=?")
        values.append(name)
    if description is not None:
        fields.append("description=?")
        values.append(description)
    if draft is not None:
        fields.append("draft=?")
        values.append(_dumps(draft))
    if not fields:
        return
    fields.append("updated_at=CURRENT_TIMESTAMP")
    conn.execute(f"UPDATE pipelines SET {', '.join(fields)} WHERE id=?", (*values, pipeline_id))


def archive_pipeline(conn, pipeline_id: str) -> None:
    conn.execute("UPDATE pipelines SET archived=1, updated_at=CURRENT_TIMESTAMP WHERE id=?", (pipeline_id,))


# ------------------------------------------------------------------ versions
def latest_version(conn, pipeline_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM pipeline_versions WHERE pipeline_id=? ORDER BY version DESC LIMIT 1", (pipeline_id,)
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["graph"] = _loads(item.get("graph"), {})
    return item


def get_version(conn, pipeline_id: str, version: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM pipeline_versions WHERE pipeline_id=? AND version=?", (pipeline_id, int(version))
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["graph"] = _loads(item.get("graph"), {})
    return item


def list_versions(conn, pipeline_id: str) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT pipeline_id, version, graph_hash, created_at FROM pipeline_versions WHERE pipeline_id=? "
        "ORDER BY version DESC",
        (pipeline_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def freeze_version(conn, pipeline_id: str, graph: Dict[str, Any], graph_hash: str) -> int:
    """The version holding ``graph``: the latest one when it is unchanged, otherwise a new one."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT version, graph_hash FROM pipeline_versions WHERE pipeline_id=? ORDER BY version DESC LIMIT 1",
            (pipeline_id,),
        ).fetchone()
        if row and row["graph_hash"] == graph_hash:
            conn.execute("COMMIT")
            return int(row["version"])
        version = (int(row["version"]) if row else 0) + 1
        conn.execute(
            "INSERT INTO pipeline_versions (pipeline_id, version, graph, graph_hash) VALUES (?, ?, ?, ?)",
            (pipeline_id, version, _dumps(graph), graph_hash),
        )
        conn.execute(
            "UPDATE pipelines SET latest_version=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (version, pipeline_id)
        )
        conn.execute("COMMIT")
        return version
    except Exception:
        conn.execute("ROLLBACK")
        raise


# ------------------------------------------------------------------ runs
def _run_row(row: Any) -> Dict[str, Any]:
    item = dict(row)
    item["inputs"] = _loads(item.get("inputs"), {})
    item["cancel_requested"] = bool(item.get("cancel_requested"))
    return item


def create_run(
    conn, *, pipeline_id: str, version: int, owner_id: str, session_id: str, inputs: Dict[str, Any]
) -> str:
    run_id = "plr_" + uuid.uuid4().hex[:16]
    conn.execute(
        "INSERT INTO pipeline_runs (id, pipeline_id, version, owner_id, session_id, status, inputs) "
        "VALUES (?, ?, ?, ?, ?, 'queued', ?)",
        (run_id, pipeline_id, int(version), owner_id, session_id, _dumps(inputs)),
    )
    return run_id


def get_run(conn, run_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
    return _run_row(row) if row else None


def list_runs(conn, pipeline_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM pipeline_runs WHERE pipeline_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (pipeline_id, int(limit)),
    ).fetchall()
    return [_run_row(row) for row in rows]


def count_active_runs(conn, pipeline_id: Optional[str] = None) -> int:
    if pipeline_id:
        row = conn.execute(
            "SELECT COUNT(*) FROM pipeline_runs WHERE pipeline_id=? AND status IN ('queued','running')",
            (pipeline_id,),
        ).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) FROM pipeline_runs WHERE status IN ('queued','running')").fetchone()
    return int(row[0])


def mark_run_started(conn, run_id: str) -> None:
    conn.execute(
        "UPDATE pipeline_runs SET status='running', started_at=CURRENT_TIMESTAMP WHERE id=? AND status='queued'",
        (run_id,),
    )


def finish_run(conn, run_id: str, status: str, error: Optional[str] = None) -> None:
    conn.execute(
        "UPDATE pipeline_runs SET status=?, error=?, finished_at=CURRENT_TIMESTAMP "
        "WHERE id=? AND status IN ('queued','running')",
        (status, error, run_id),
    )


def request_cancel(conn, run_id: str) -> bool:
    cursor = conn.execute(
        "UPDATE pipeline_runs SET cancel_requested=1 WHERE id=? AND status IN ('queued','running')", (run_id,)
    )
    return cursor.rowcount > 0


def cancel_requested(conn, run_id: str) -> bool:
    row = conn.execute("SELECT cancel_requested FROM pipeline_runs WHERE id=?", (run_id,)).fetchone()
    return bool(row and row[0])


def fail_interrupted_runs(conn) -> int:
    """After a restart no worker owns a queued or running run; mark it and its unfinished nodes failed."""
    rows = conn.execute("SELECT id FROM pipeline_runs WHERE status IN ('queued','running')").fetchall()
    run_ids = [row[0] for row in rows]
    for run_id in run_ids:
        conn.execute(
            "UPDATE pipeline_node_runs SET status='failed', error='interrupted by a server restart', "
            "finished_at=CURRENT_TIMESTAMP WHERE run_id=? AND status IN ('pending','running')",
            (run_id,),
        )
        conn.execute(
            "UPDATE pipeline_runs SET status='failed', error='interrupted by a server restart', "
            "finished_at=CURRENT_TIMESTAMP WHERE id=?",
            (run_id,),
        )
    return len(run_ids)


# ------------------------------------------------------------------ node runs
def _node_row(row: Any) -> Dict[str, Any]:
    item = dict(row)
    item["outputs"] = _loads(item.get("outputs"), {})
    item["detail"] = _loads(item.get("detail"), {})
    return item


def init_node_runs(conn, run_id: str, node_ids: List[str]) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO pipeline_node_runs (run_id, node_id, status) VALUES (?, ?, 'pending')",
        [(run_id, node_id) for node_id in node_ids],
    )


def set_node_running(conn, run_id: str, node_id: str, cache_key: Optional[str]) -> None:
    conn.execute(
        "UPDATE pipeline_node_runs SET status='running', cache_key=?, started_at=CURRENT_TIMESTAMP "
        "WHERE run_id=? AND node_id=?",
        (cache_key, run_id, node_id),
    )


def finish_node(
    conn,
    run_id: str,
    node_id: str,
    status: str,
    *,
    outputs: Optional[Dict[str, Any]] = None,
    detail: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    cache_key: Optional[str] = None,
) -> None:
    conn.execute(
        "UPDATE pipeline_node_runs SET status=?, outputs=?, detail=?, error=?, "
        "cache_key=COALESCE(?, cache_key), "
        "started_at=COALESCE(started_at, CURRENT_TIMESTAMP), finished_at=CURRENT_TIMESTAMP "
        "WHERE run_id=? AND node_id=?",
        (
            status,
            _dumps(outputs or {}),
            _dumps(detail or {}),
            error,
            cache_key,
            run_id,
            node_id,
        ),
    )


def list_node_runs(conn, run_id: str) -> List[Dict[str, Any]]:
    rows = conn.execute("SELECT * FROM pipeline_node_runs WHERE run_id=? ORDER BY rowid", (run_id,)).fetchall()
    return [_node_row(row) for row in rows]


def get_node_run(conn, run_id: str, node_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM pipeline_node_runs WHERE run_id=? AND node_id=?", (run_id, node_id)
    ).fetchone()
    return _node_row(row) if row else None


def find_cached_outputs(conn, owner_id: str, cache_key: str) -> List[Dict[str, Any]]:
    """Outputs of this owner's earlier node runs with the same cache key, newest first."""
    rows = conn.execute(
        """SELECT n.outputs, n.run_id, n.node_id FROM pipeline_node_runs n
           JOIN pipeline_runs r ON r.id = n.run_id
           WHERE n.cache_key=? AND n.status='succeeded' AND r.owner_id=?
           ORDER BY n.finished_at DESC LIMIT 5""",
        (cache_key, owner_id),
    ).fetchall()
    return [
        {"outputs": _loads(row["outputs"], {}), "run_id": row["run_id"], "node_id": row["node_id"]} for row in rows
    ]
