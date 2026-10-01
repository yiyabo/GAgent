"""Durable tool-step metadata and checkpoint pointers; payloads live outside SQLite.

The parent chat-run lease fences every write in the same SQLite transaction.
This ledger does not make external tool side effects exactly-once.
"""
from __future__ import annotations

import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Literal, Optional

from app.database import get_db
from app.repository.chat_runs import _owns_active_run, _write_transaction
from app.services.chat_run_state import chat_run_claim
from app.services.cancellation import current_cancel_token

StepStatus = Literal["submitted", "running", "succeeded", "failed", "interrupted"]
ReplayPolicy = Literal["read_only", "idempotent", "mutating"]
STEP_STATUSES = {"submitted", "running", "succeeded", "failed", "interrupted"}
REPLAY_POLICIES = {"read_only", "idempotent", "mutating"}
CHECKPOINT_RETENTION = 3


class StaleRunClaim(RuntimeError):
    """The caller lacks the current, live parent-run claim."""


class ClosedRunContext(StaleRunClaim):
    """The owning run scope has exited, even if its SQL lease is still live."""


class StepStateConflict(RuntimeError):
    """The requested transition cannot overwrite the recorded step."""


@dataclass(frozen=True)
class StepKey:
    run_id: str
    tool_call_id: str
    params_fingerprint: str
    attempt: int = 1

    def __post_init__(self) -> None:
        if not self.run_id or not self.tool_call_id or len(self.tool_call_id) > 512:
            raise ValueError("run_id and bounded tool_call_id are required")
        if not re.fullmatch(r"[0-9a-f]{64}", self.params_fingerprint):
            raise ValueError("params_fingerprint must be a SHA256 digest")
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int) or self.attempt < 1:
            raise ValueError("attempt must be a positive integer")

    def sql_args(self) -> tuple:
        return self.run_id, self.tool_call_id, self.params_fingerprint, self.attempt


@dataclass(frozen=True)
class RunStep:
    key: StepKey
    tool_name: str
    replay_policy: ReplayPolicy
    status: StepStatus
    worker_id: Optional[str]
    result_ref: Optional[str]
    result_checksum: Optional[str]
    output_refs: tuple[dict[str, Any], ...]
    error_code: Optional[str]
    created_at: str
    started_at: Optional[str]
    finished_at: Optional[str]


def ensure_run_step_schema(conn: Any) -> None:
    """Additive and idempotent; called explicitly by init_db, never on import."""
    conn.execute("""CREATE TABLE IF NOT EXISTS run_steps (
        run_id TEXT NOT NULL, tool_call_id TEXT NOT NULL,
        params_fingerprint TEXT NOT NULL, attempt INTEGER NOT NULL CHECK(attempt > 0),
        tool_name TEXT NOT NULL,
        replay_policy TEXT NOT NULL CHECK(replay_policy IN ('read_only','idempotent','mutating')),
        status TEXT NOT NULL CHECK(status IN ('submitted','running','succeeded','failed','interrupted')),
        worker_id TEXT, result_ref TEXT, result_checksum TEXT,
        output_refs_json TEXT NOT NULL DEFAULT '[]', error_code TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        started_at TIMESTAMP, finished_at TIMESTAMP,
        PRIMARY KEY (run_id, tool_call_id, params_fingerprint, attempt),
        FOREIGN KEY (run_id) REFERENCES chat_runs(run_id) ON DELETE CASCADE
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_run_steps_run_status ON run_steps(run_id,status)")
    with _write_transaction(conn):
        columns = {row['name'] for row in conn.execute('PRAGMA table_info(run_checkpoints)').fetchall()}
        legacy = bool(columns and 'checkpoint_key' not in columns)
        if legacy:
            conn.execute('ALTER TABLE run_checkpoints RENAME TO run_checkpoints_legacy')
        conn.execute("""CREATE TABLE IF NOT EXISTS run_checkpoints (
            run_id TEXT NOT NULL, checkpoint_key TEXT NOT NULL DEFAULT 'controller',
            version INTEGER NOT NULL CHECK(version > 0),
            schema_version INTEGER NOT NULL, checkpoint_ref TEXT NOT NULL,
            checksum TEXT NOT NULL, worker_id TEXT NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (run_id,checkpoint_key),
            FOREIGN KEY (run_id) REFERENCES chat_runs(run_id) ON DELETE CASCADE
        )""")
        if legacy:
            conn.execute("""INSERT INTO run_checkpoints(run_id,checkpoint_key,version,schema_version,
                checkpoint_ref,checksum,worker_id,updated_at)
                SELECT run_id,'controller',version,schema_version,checkpoint_ref,checksum,worker_id,updated_at
                FROM run_checkpoints_legacy""")
            conn.execute('DROP TABLE run_checkpoints_legacy')
        conn.execute("""CREATE TABLE IF NOT EXISTS run_checkpoint_history (
            run_id TEXT NOT NULL, checkpoint_key TEXT NOT NULL,
            version INTEGER NOT NULL, checkpoint_ref TEXT NOT NULL,
            checksum TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (run_id,checkpoint_key,version),
            FOREIGN KEY (run_id,checkpoint_key) REFERENCES run_checkpoints(run_id,checkpoint_key) ON DELETE CASCADE
        )""")
        # Preserve the previous deployment's latest pointer as a committed
        # controller snapshot; repeated startup never adds duplicate history.
        conn.execute("""INSERT OR IGNORE INTO run_checkpoint_history(run_id,checkpoint_key,version,checkpoint_ref,checksum,created_at)
            SELECT run_id,checkpoint_key,version,checkpoint_ref,checksum,updated_at FROM run_checkpoints""")


def _worker(run_id: str, worker_id: Optional[str]) -> str:
    inherited = chat_run_claim.get()
    if inherited is not None:
        if inherited[0] != run_id or (worker_id is not None and inherited[1] != worker_id):
            raise StaleRunClaim("step claim differs from inherited run claim")
        worker_id = inherited[1]
    if not worker_id:
        raise StaleRunClaim("durable steps require a parent-run claim")
    return worker_id


@contextmanager
def _owned_write(run_id: str, worker_id: Optional[str], *, cleanup: bool = False) -> Iterator[tuple[Any, str]]:
    _assert_context_open(cleanup=cleanup)
    claim = _worker(run_id, worker_id)
    with get_db() as conn:
        with _write_transaction(conn):
            if not _owns_active_run(conn, run_id, claim):
                raise StaleRunClaim("parent run claim is no longer live")
            yield conn, claim
            # A delayed host thread can outlive cancellation/worker teardown.
            # Closing while SQL runs rolls the write back before commit.
            _assert_context_open(cleanup=cleanup)


def _assert_context_open(*, cleanup: bool = False) -> None:
    token = current_cancel_token()
    if token is None:
        return
    if token.closed:
        raise ClosedRunContext('parent run context is closed')
    if not cleanup and token.is_set():
        raise StaleRunClaim('parent run context is cancelled')


def assert_run_owned(run_id: str, *, worker_id: Optional[str] = None) -> None:
    with _owned_write(run_id, worker_id):
        pass


_KEY_WHERE = "run_id=? AND tool_call_id=? AND params_fingerprint=? AND attempt=?"


def _record(row: Any) -> Optional[RunStep]:
    if row is None:
        return None
    return RunStep(
        key=StepKey(str(row["run_id"]), str(row["tool_call_id"]), str(row["params_fingerprint"]), int(row["attempt"])),
        tool_name=str(row["tool_name"]), replay_policy=row["replay_policy"], status=row["status"],
        worker_id=row["worker_id"], result_ref=row["result_ref"], result_checksum=row["result_checksum"],
        output_refs=tuple(json.loads(row["output_refs_json"])), error_code=row["error_code"],
        created_at=row["created_at"], started_at=row["started_at"], finished_at=row["finished_at"],
    )


def get_step(key: StepKey) -> Optional[RunStep]:
    with get_db() as conn:
        return _record(conn.execute(f"SELECT * FROM run_steps WHERE {_KEY_WHERE}", key.sql_args()).fetchone())


def latest_step(run_id: str, tool_call_id: str, params_fingerprint: str) -> Optional[RunStep]:
    with get_db() as conn:
        return _record(conn.execute("""SELECT * FROM run_steps WHERE run_id=? AND tool_call_id=?
            AND params_fingerprint=? ORDER BY attempt DESC LIMIT 1""", (run_id, tool_call_id, params_fingerprint)).fetchone())


def list_steps(run_id: str) -> list[RunStep]:
    with get_db() as conn:
        return [_record(row) for row in conn.execute("SELECT * FROM run_steps WHERE run_id=? ORDER BY created_at,tool_call_id,attempt", (run_id,)).fetchall()]


def submit_step(key: StepKey, tool_name: str, replay_policy: ReplayPolicy, *, worker_id: Optional[str] = None) -> RunStep:
    if replay_policy not in REPLAY_POLICIES or not tool_name:
        raise ValueError("tool_name and an explicit replay policy are required")
    with _owned_write(key.run_id, worker_id) as (conn, _claim):
        conn.execute("""INSERT INTO run_steps(run_id,tool_call_id,params_fingerprint,attempt,tool_name,replay_policy,status)
            VALUES (?,?,?,?,?,?,'submitted') ON CONFLICT DO NOTHING""", (*key.sql_args(), tool_name, replay_policy))
        record = _record(conn.execute(f"SELECT * FROM run_steps WHERE {_KEY_WHERE}", key.sql_args()).fetchone())
        if record.tool_name != tool_name or record.replay_policy != replay_policy:
            raise StepStateConflict("step identity already has a different tool or replay policy")
        return record


def claim_step(key: StepKey, *, worker_id: Optional[str] = None) -> bool:
    with _owned_write(key.run_id, worker_id) as (conn, claim):
        cursor = conn.execute(f"""UPDATE run_steps SET status='running', worker_id=?, started_at=CURRENT_TIMESTAMP
            WHERE {_KEY_WHERE} AND status='submitted'""", (claim, *key.sql_args()))
        return cursor.rowcount == 1


def _valid_ref(ref: str, checksum: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{32}", ref) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ValueError("result/checkpoint references must be opaque IDs with SHA256 checksums")


def _error_code(code: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", code):
        raise ValueError("error_code must be a bounded code, not an exception payload")
    return code


def _output_refs_json(refs: tuple[dict, ...]) -> str:
    for ref in refs:
        if set(ref) != {'path', 'checksum'} or not isinstance(ref['path'], str) or not ref['path'].startswith('/') or len(ref['path']) > 4096:
            raise ValueError('output references require an absolute path and checksum only')
        if not re.fullmatch(r'[0-9a-f]{64}', ref['checksum']):
            raise ValueError('output reference requires a SHA256 checksum')
    return json.dumps(refs, ensure_ascii=False, sort_keys=True, allow_nan=False)


def finish_step(key: StepKey, *, result_ref: str, checksum: str, output_refs: tuple[dict, ...] = (), worker_id: Optional[str] = None) -> RunStep:
    _valid_ref(result_ref, checksum)
    refs_json = _output_refs_json(output_refs)
    with _owned_write(key.run_id, worker_id) as (conn, claim):
        cursor = conn.execute(f"""UPDATE run_steps SET status='succeeded',result_ref=?,result_checksum=?,
            output_refs_json=?,error_code=NULL,finished_at=CURRENT_TIMESTAMP
            WHERE {_KEY_WHERE} AND status='running' AND worker_id=?""", (result_ref, checksum, refs_json, *key.sql_args(), claim))
        record = _record(conn.execute(f"SELECT * FROM run_steps WHERE {_KEY_WHERE}", key.sql_args()).fetchone())
        if cursor.rowcount != 1:
            if record is None or record.status != 'succeeded' or record.worker_id != claim or record.result_ref != result_ref or record.result_checksum != checksum:
                raise StepStateConflict("only the claimed running step can finish")
        return record


def end_step(key: StepKey, status: Literal['failed', 'interrupted'], *, error_code: str, worker_id: Optional[str] = None, expected_step_worker: Optional[str] = None) -> RunStep:
    if status not in {'failed', 'interrupted'}:
        raise ValueError("end_step requires failed or interrupted")
    error_code = _error_code(error_code)
    with _owned_write(key.run_id, worker_id, cleanup=True) as (conn, claim):
        target = expected_step_worker if expected_step_worker is not None else claim
        cursor = conn.execute(f"""UPDATE run_steps SET status=?,error_code=?,finished_at=CURRENT_TIMESTAMP
            WHERE {_KEY_WHERE} AND status IN ('submitted','running') AND (worker_id=? OR worker_id IS NULL)""", (status, error_code, *key.sql_args(), target))
        record = _record(conn.execute(f"SELECT * FROM run_steps WHERE {_KEY_WHERE}", key.sql_args()).fetchone())
        if cursor.rowcount != 1 and (record is None or record.status != status):
            raise StepStateConflict("step already has a different terminal state")
        return record


def reconcile_step(key: StepKey, *, result_ref: str, checksum: str, output_refs: tuple[dict, ...] = (), worker_id: Optional[str] = None) -> RunStep:
    """Explicit caller-verified result; never invokes or retries an external tool."""
    _valid_ref(result_ref, checksum)
    refs_json = _output_refs_json(output_refs)
    with _owned_write(key.run_id, worker_id) as (conn, claim):
        cursor = conn.execute(f"""UPDATE run_steps SET status='succeeded',worker_id=?,result_ref=?,result_checksum=?,
            output_refs_json=?,error_code=NULL,finished_at=CURRENT_TIMESTAMP
            WHERE {_KEY_WHERE} AND status IN ('running','failed','interrupted','succeeded')""", (claim, result_ref, checksum, refs_json, *key.sql_args()))
        if cursor.rowcount != 1:
            raise StepStateConflict("reconciliation requires a recorded attempt")
        return _record(conn.execute(f"SELECT * FROM run_steps WHERE {_KEY_WHERE}", key.sql_args()).fetchone())


def _checkpoint_key(key: str) -> str:
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,512}', key):
        raise ValueError('checkpoint_key must be a bounded scope identifier')
    return key


def save_checkpoint_pointer(run_id: str, ref: str, checksum: str, *, schema_version: int = 1, expected_version: Optional[int] = None, worker_id: Optional[str] = None, checkpoint_key: str = 'controller') -> int:
    _valid_ref(ref, checksum)
    checkpoint_key = _checkpoint_key(checkpoint_key)
    with _owned_write(run_id, worker_id) as (conn, claim):
        row = conn.execute("SELECT version FROM run_checkpoints WHERE run_id=? AND checkpoint_key=?", (run_id, checkpoint_key)).fetchone()
        current = int(row['version']) if row else 0
        if expected_version is not None and expected_version != current:
            raise StepStateConflict("checkpoint version changed")
        version = current + 1
        conn.execute("""INSERT INTO run_checkpoints(run_id,checkpoint_key,version,schema_version,checkpoint_ref,checksum,worker_id)
            VALUES (?,?,?,?,?,?,?) ON CONFLICT(run_id,checkpoint_key) DO UPDATE SET version=excluded.version,
            schema_version=excluded.schema_version,checkpoint_ref=excluded.checkpoint_ref,
            checksum=excluded.checksum,worker_id=excluded.worker_id,updated_at=CURRENT_TIMESTAMP""", (run_id, checkpoint_key, version, schema_version, ref, checksum, claim))
        conn.execute("""INSERT INTO run_checkpoint_history(run_id,checkpoint_key,version,checkpoint_ref,checksum)
            VALUES (?,?,?,?,?)""", (run_id, checkpoint_key, version, ref, checksum))
        conn.execute("""DELETE FROM run_checkpoint_history WHERE run_id=? AND checkpoint_key=?
            AND version NOT IN (SELECT version FROM run_checkpoint_history WHERE run_id=? AND checkpoint_key=?
                ORDER BY version DESC LIMIT ?)""", (run_id, checkpoint_key, run_id, checkpoint_key, CHECKPOINT_RETENTION))
        return version


def load_checkpoint_pointer(run_id: str, checkpoint_key: str = 'controller') -> Optional[dict[str, Any]]:
    checkpoint_key = _checkpoint_key(checkpoint_key)
    with get_db() as conn:
        row = conn.execute("SELECT * FROM run_checkpoints WHERE run_id=? AND checkpoint_key=?", (run_id, checkpoint_key)).fetchone()
        return dict(row) if row else None


def list_checkpoint_pointers(run_id: str) -> list[dict[str, Any]]:
    with get_db() as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM run_checkpoints WHERE run_id=? ORDER BY checkpoint_key', (run_id,)).fetchall()]


def list_checkpoint_history(run_id: str, checkpoint_key: str = 'controller') -> list[dict[str, Any]]:
    checkpoint_key = _checkpoint_key(checkpoint_key)
    with get_db() as conn:
        return [dict(row) for row in conn.execute("""SELECT * FROM run_checkpoint_history WHERE run_id=? AND checkpoint_key=?
            ORDER BY version DESC""", (run_id, checkpoint_key)).fetchall()]


def is_blob_referenced(ref: str) -> bool:
    """Protect observations, other scopes, imported results and source snapshots."""
    with get_db() as conn:
        return conn.execute("""SELECT 1 FROM run_steps WHERE result_ref=?
            UNION ALL SELECT 1 FROM run_checkpoints WHERE checkpoint_ref=?
            UNION ALL SELECT 1 FROM run_checkpoint_history WHERE checkpoint_ref=? LIMIT 1""", (ref, ref, ref)).fetchone() is not None


def import_steps(source_run_id: str, target_run_id: str, *, worker_id: Optional[str] = None) -> int:
    """Copy observations, never revive or modify the source run's terminal state."""
    if source_run_id == target_run_id:
        raise ValueError('resume import requires a new run id')
    with _owned_write(target_run_id, worker_id) as (conn, claim):
        source = conn.execute('SELECT session_id,owner_id,status FROM chat_runs WHERE run_id=?', (source_run_id,)).fetchone()
        target = conn.execute('SELECT session_id,owner_id FROM chat_runs WHERE run_id=?', (target_run_id,)).fetchone()
        if source is None or source['status'] not in {'succeeded', 'failed', 'cancelled'}:
            raise StepStateConflict('resume source must be a terminal run')
        if source['session_id'] != target['session_id'] or source['owner_id'] != target['owner_id']:
            raise StepStateConflict('resume source belongs to another session or owner')
        cursor = conn.execute("""INSERT INTO run_steps(run_id,tool_call_id,params_fingerprint,attempt,
            tool_name,replay_policy,status,worker_id,result_ref,result_checksum,output_refs_json,error_code,started_at,finished_at)
            SELECT ?,tool_call_id,params_fingerprint,attempt,tool_name,replay_policy,
                CASE WHEN status='running' THEN 'interrupted' ELSE status END,
                ?,result_ref,result_checksum,output_refs_json,
                CASE WHEN status='running' THEN 'resume_imported_inflight' ELSE error_code END,started_at,
                CASE WHEN status='running' THEN CURRENT_TIMESTAMP ELSE finished_at END
            FROM run_steps WHERE run_id=? AND 1
            ON CONFLICT DO NOTHING""", (target_run_id, claim, source_run_id))
        return cursor.rowcount
