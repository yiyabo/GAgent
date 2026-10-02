"""Read scoped notes and original conversation evidence without model calls.

Existing global notes are linked by session:<id> tags; legacy session stores
remain readable. Scope predicates run before ranking/limits, not afterwards.
"""
from __future__ import annotations

from contextlib import closing
import sqlite3

from app.database import get_db
from app.config.database_config import get_database_config


def session_scope(session_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT id,owner_id,project_id FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
        return dict(row) if row else None


def _scope_sql(scope: dict, alias: str = "s") -> tuple[str, list]:
    # A projectless conversation never shares all other projectless histories.
    return (f"{alias}.owner_id=? AND ({alias}.id=? OR (? IS NOT NULL AND {alias}.project_id=?))",
            [scope["owner_id"], scope["id"], scope["project_id"], scope["project_id"]])


def _matches(terms: list[str], columns: tuple[str, ...]) -> tuple[str, list]:
    clauses, params = [], []
    for term in terms[:16]:
        clauses.append("(" + " OR ".join(f"instr(lower(COALESCE({col},'')),?)>0" for col in columns) + ")")
        params.extend([term] * len(columns))
    return " OR ".join(clauses) or "0", params


def memory_candidates(scope: dict, terms: list[str], *, limit: int = 60) -> list[dict]:
    scoped, scope_params = _scope_sql(scope, "s")
    relevance, term_params = _matches(terms, ("m.content", "m.keywords"))
    # Malformed old tags behave as empty tags, but don't become user facts.
    tags = "CASE WHEN json_valid(m.tags) THEN m.tags ELSE '[]' END"
    source = f"EXISTS(SELECT 1 FROM json_each({tags}) t JOIN chat_sessions s ON t.value='session:'||s.id WHERE {scoped})"
    user_fact = f"(m.related_task_id IS NULL AND (m.tags IS NULL OR json_valid(m.tags)) AND NOT EXISTS(SELECT 1 FROM json_each({tags}) t WHERE t.value LIKE 'session:%'))"
    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories'").fetchone():
            rows = []
        else:
            rows = [dict(row) for row in conn.execute(f"""WITH eligible AS (SELECT m.*,
                CASE WHEN {user_fact} AND m.importance IN ('critical','high') THEN 1 ELSE 0 END AS user_preference,
                (SELECT s.id FROM json_each({tags}) t JOIN chat_sessions s ON t.value='session:'||s.id
                 WHERE {scoped} ORDER BY (s.id=?) DESC,s.id LIMIT 1) AS recalled_session
                FROM memories m
                WHERE m.owner_id=? AND ({source} OR {user_fact})
                AND (({relevance}) OR ({user_fact} AND m.importance IN ('critical','high')))
                ), ranked AS (
                    SELECT *, ROW_NUMBER() OVER(PARTITION BY user_preference
                        ORDER BY (importance='critical') DESC,created_at DESC,id) AS recall_rank
                    FROM eligible
                ) SELECT * FROM ranked
                WHERE (user_preference=1 AND recall_rank<=2) OR (user_preference=0 AND recall_rank<=?)""",
                [*scope_params, scope["id"], scope["owner_id"], *scope_params, *term_params, limit]).fetchall()]
    for row in rows:
        row["source_store"] = "main"
        row["source_session_id"] = row.pop("recalled_session", None)
        row["scope"] = "session" if row["source_session_id"] == scope["id"] else "project" if row["source_session_id"] else "user"

    # Do not create empty SQLite files during recall. Legacy stores are read-only.
    path = get_database_config().get_session_db_path(scope["id"])
    if path.is_file():
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as conn:
            conn.row_factory = sqlite3.Row
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories'").fetchone():
                columns = {r[1] for r in conn.execute("PRAGMA table_info(memories)")}
                owner = "(m.owner_id=? OR m.owner_id IS NULL)" if "owner_id" in columns else "1"
                params = [scope["owner_id"]] if "owner_id" in columns else []
                legacy_match, legacy_params = _matches(terms, ("m.content", "m.keywords"))
                for raw in conn.execute(f"SELECT m.* FROM memories m WHERE {owner} AND (({legacy_match}) OR m.importance IN ('critical','high')) ORDER BY m.created_at DESC,m.id LIMIT ?", [*params, *legacy_params, limit]):
                    rows.append({**dict(raw), "source_store": "session", "source_session_id": scope["id"], "scope": "session"})
    return rows


def history_candidates(scope: dict, terms: list[str], *, client_message_id: str | None = None, current_message: str | None = None, limit: int = 60) -> list[dict]:
    scoped, params = _scope_sql(scope)
    relevance, term_params = _matches(terms, ("m.content",))
    if terms:
        scoped += f" AND ({relevance})"
        params.extend(term_params)
    if client_message_id:
        scoped += " AND NOT (m.session_id=? AND json_valid(m.metadata) AND COALESCE(json_extract(m.metadata,'$.client_message_id'),'')=?)"
        params.extend([scope["id"], client_message_id])
    if current_message:
        scoped += " AND NOT (m.session_id=? AND m.role='user' AND m.content=?)"
        params.extend([scope["id"], current_message])
    with get_db() as conn:
        return [dict(row) for row in conn.execute(f"""SELECT m.id,m.session_id,m.role,m.content,m.metadata,m.created_at,s.name AS session_title
            FROM chat_messages m JOIN chat_sessions s ON s.id=m.session_id
            WHERE {scoped} AND m.role IN ('user','assistant') AND length(trim(m.content))>0
            ORDER BY m.id DESC LIMIT ?""", [*params, limit]).fetchall()]


def history_neighbors(scope: dict, anchors: list[dict], *, client_message_id: str | None = None, current_message: str | None = None) -> list[dict]:
    """Include a matched request's answer (or an answer's original request)."""
    rows = []
    scoped, scope_params = _scope_sql(scope)
    excluded = ""
    excluded_params = []
    if client_message_id:
        excluded += " AND NOT (m.session_id=? AND json_valid(m.metadata) AND COALESCE(json_extract(m.metadata,'$.client_message_id'),'')=?)"
        excluded_params.extend([scope['id'], client_message_id])
    if current_message:
        excluded += " AND NOT (m.session_id=? AND m.role='user' AND m.content=?)"
        excluded_params.extend([scope['id'], current_message])
    with get_db() as conn:
        for anchor in anchors[:3]:
            comparison, order = ('>', 'ASC') if anchor['role'] == 'user' else ('<', 'DESC')
            row = conn.execute(f"""SELECT m.id,m.session_id,m.role,m.content,m.metadata,m.created_at,s.name AS session_title
                FROM chat_messages m JOIN chat_sessions s ON s.id=m.session_id
                WHERE {scoped} AND m.session_id=? AND m.id {comparison} ?
                AND m.role IN ('user','assistant') {excluded}
                ORDER BY m.id {order} LIMIT 1""",
                [*scope_params, anchor['session_id'], anchor['id'], *excluded_params]).fetchone()
            # A later user turn is not the requested answer; do not skip across it.
            if row and row['role'] != anchor['role'] and str(row['content']).strip():
                rows.append(dict(row))
    return rows


def terminal_turns_without_answer(session_id: str, owner_id: str, message_ids: list[int]) -> list[dict]:
    if not message_ids:
        return []
    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_runs'").fetchone():
            return []
        placeholders = ','.join('?' for _ in message_ids)
        return [dict(row) for row in conn.execute(f"""SELECT run_id,status,user_message_id FROM chat_runs
            WHERE session_id=? AND owner_id=? AND assistant_message_id IS NULL
            AND status IN ('failed','cancelled') AND user_message_id IN ({placeholders})""",
            [session_id,owner_id,*message_ids]).fetchall()]
