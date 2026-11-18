"""
Integrated Memory Service.

Combines Memory-MCP capabilities with existing system infrastructure
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from ...config.database_config import get_database_config
from ...database import get_db
from ...llm import get_default_client
from ...models_memory import (
    ImportanceLevel,
    MemoryItem,
    MemoryNote,
    MemoryStats,
    MemoryType,
    QueryMemoryRequest,
    QueryMemoryResponse,
    SaveMemoryRequest,
    SaveMemoryResponse,
)
from ..embeddings import get_embeddings_service

logger = logging.getLogger(__name__)


def _coerce_memory_embedding_for_query(
    query_embedding: list[float],
    raw_vector: Any,
) -> list[float] | None:
    """
    Return stored embedding as list only if its length matches the query vector.
    Avoids silent truncation in semantic search (incompatible with SimilarityCalculator).
    """
    if not query_embedding:
        return None
    expected = len(query_embedding)
    if isinstance(raw_vector, str):
        try:
            parsed = json.loads(raw_vector)
        except (json.JSONDecodeError, TypeError):
            return None
        raw_vector = parsed
    if not isinstance(raw_vector, list) or len(raw_vector) != expected:
        return None
    return raw_vector


def _ensure_owner_column(conn: sqlite3.Connection) -> None:
    """Idempotent migration: memories.owner_id + index (recall is owner-isolated)."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
    if "owner_id" not in cols:
        conn.execute("ALTER TABLE memories ADD COLUMN owner_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_owner ON memories(owner_id)")
    conn.commit()


def resolve_owner_id_for_session(session_id: str | None) -> str | None:
    """Look up the owning user of a chat session (None when unknown)."""
    if not session_id:
        return None
    try:
        with get_db() as conn:
            row = conn.execute("SELECT owner_id FROM chat_sessions WHERE id = ?", (session_id,)).fetchone()
        if row and row[0]:
            return str(row[0])
    except Exception as exc:
        logger.warning("resolve_owner_id_for_session failed for %s: %s", session_id, exc)
    return None


DEDUPE_EMBEDDING_TIMEOUT_S = 5.0
_IMPORTANCE_RANK = {
    "temporary": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


class MemoryAccessDenied(PermissionError):
    """Raised when a caller tries to reach a memory store it does not own."""


def normalize_memory_content(text: Any) -> str:
    """Case/whitespace/punctuation-insensitive form used for exact dedupe."""
    lowered = str(text or "").casefold()
    lowered = re.sub(r"[\s\u3000]+", " ", lowered)
    lowered = re.sub(r"[^\w\u4e00-\u9fff ]+", "", lowered)
    return lowered.strip()


def _json_str_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        values = raw
    else:
        try:
            values = json.loads(raw or "[]")
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(values, list):
        return []
    return [str(v) for v in values if str(v).strip()]


def _merge_unique(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen = set()
    for group in groups:
        for value in group or []:
            text = str(value).strip()
            key = text.casefold()
            if text and key not in seen:
                seen.add(key)
                merged.append(text)
    return merged


def _higher_importance(a: Any, b: Any) -> str:
    av, bv = str(a or "medium"), str(b or "medium")
    return av if _IMPORTANCE_RANK.get(av, 2) >= _IMPORTANCE_RANK.get(bv, 2) else bv


def _memory_row_to_item(row: sqlite3.Row) -> dict[str, Any]:
    keys = row.keys()
    return {
        "id": row["id"],
        "content": row["content"],
        "memory_type": row["memory_type"],
        "importance": row["importance"],
        "keywords": _json_str_list(row["keywords"]),
        "tags": _json_str_list(row["tags"]),
        "context": row["context"] if "context" in keys else None,
        "created_at": row["created_at"],
        "last_accessed": row["last_accessed"] if "last_accessed" in keys else None,
        "retrieval_count": int((row["retrieval_count"] if "retrieval_count" in keys else 0) or 0),
        "embedding_generated": bool(row["embedding_generated"] if "embedding_generated" in keys else False),
    }


def _invalidate_memory_cache() -> None:
    """Best-effort clear of cached memory-derived payloads after mutations."""
    try:
        from . import unified_cache

        cache = getattr(unified_cache, "_unified_cache", None)
        if cache is not None:
            cache.invalidate_namespace("memory")
    except Exception as exc:  # pragma: no cover - cache is advisory
        logger.debug("unified_cache invalidation skipped: %s", exc)


def mark_memories_recalled(conn: sqlite3.Connection, memory_ids: list[str]) -> None:
    """Atomically bump retrieval_count/last_accessed on recalled rows."""
    ids = [str(i) for i in memory_ids if i]
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    try:
        conn.execute(
            f"UPDATE memories SET retrieval_count = COALESCE(retrieval_count, 0) + 1, "
            f"last_accessed = CURRENT_TIMESTAMP WHERE id IN ({placeholders})",
            tuple(ids),
        )
        conn.commit()
    except sqlite3.Error as exc:
        logger.warning("Failed to update retrieval_count: %s", exc)


PLAN_TAG_PREFIX = "plan:"
SESSION_TAG_PREFIX = "session:"


def coerce_positive_id(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def resolve_owner_id_for_plan_task(plan_id: Any, task_id: Any = None) -> str | None:
    """Resolve the owning user of a plan task from authoritative plan context.

    Task ids are plan-local: every plan shard (``plan_<id>.sqlite``) numbers
    its tasks from 1, so a bare ``task_id`` is ambiguous across plans and must
    never be used to infer ownership. The caller has to supply the plan id the
    task was loaded from; the owner is read from the main ``plans`` table.

    When ``task_id`` is given it must exist in that plan's shard, otherwise the
    (plan, task) pair is treated as unknown. Returns ``None`` whenever the owner
    cannot be established (missing plan id, unknown plan, ownerless plan,
    missing shard/task, or lookup errors) so callers can skip the write.
    Results are not cached: plan ownership is read fresh on every call.
    """
    pid = coerce_positive_id(plan_id)
    if pid is None:
        return None
    tid: int | None = None
    if task_id is not None:
        tid = coerce_positive_id(task_id)
        if tid is None:
            return None
    try:
        with get_db() as conn:
            row = conn.execute("SELECT owner FROM plans WHERE id = ?", (pid,)).fetchone()
        owner = str(row[0]).strip() if row and row[0] else ""
        if not owner:
            return None
        if tid is None:
            return owner
        from ...repository.plan_storage import get_plan_db_path

        db_path = get_plan_db_path(pid)
        if not db_path.exists():
            return None
        shard = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            hit = shard.execute("SELECT 1 FROM tasks WHERE id = ?", (tid,)).fetchone()
        except sqlite3.Error:
            hit = None
        finally:
            shard.close()
        return owner if hit else None
    except Exception as exc:
        logger.warning(
            "resolve_owner_id_for_plan_task failed for plan=%s task=%s: %s",
            plan_id,
            task_id,
            exc,
        )
        return None


class IntegratedMemoryService:
    """memoryservice - , support session"""

    def __init__(self):
        self.llm_client = get_default_client()
        self.embeddings_service = get_embeddings_service()
        self.evolution_threshold = 10  # 10memory
        self.evolution_count = 0
        self.db_config = get_database_config()

        self._ensure_memory_tables()

    @contextmanager
    def _get_conn(
        self,
        session_id: str | None = None,
        *,
        owner_id: str | None = None,
        require_owner: bool = False,
    ) -> Generator[sqlite3.Connection, None, None]:
        """Open the global store, or a per-session store.

        When ``require_owner`` is set (all user-facing reads), opening a
        per-session store is fail-closed: ``owner_id`` must be non-empty and
        the session must belong to it in ``chat_sessions``.
        """
        if require_owner:
            if not owner_id:
                raise MemoryAccessDenied("owner_id is required for memory access")
            if session_id and not self.session_belongs_to_owner(session_id, owner_id):
                raise MemoryAccessDenied("session does not belong to owner")
        if session_id:
            db_path = self.db_config.get_session_db_path(session_id)
            db_path.parent.mkdir(parents=True, exist_ok=True)

            conn = sqlite3.connect(str(db_path), isolation_level="DEFERRED")
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            try:
                self._ensure_memory_tables_for_conn(conn)
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        else:
            with get_db() as conn:
                yield conn

    def _ensure_memory_tables_for_conn(self, conn: sqlite3.Connection):
        """connectionmemoryrelateddatabase"""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                importance TEXT NOT NULL,
                keywords TEXT,
                context TEXT DEFAULT 'General',
                tags TEXT,
                related_task_id INTEGER,
                owner_id TEXT,
                links TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                retrieval_count INTEGER DEFAULT 0,
                evolution_history TEXT,
                embedding_generated BOOLEAN DEFAULT FALSE,
                embedding_model TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_embeddings (
                memory_id TEXT PRIMARY KEY,
                embedding_vector TEXT NOT NULL,
                embedding_model TEXT DEFAULT 'embedding-2',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (memory_id) REFERENCES memories (id) ON DELETE CASCADE
            )
        """)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(memory_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_task_id ON memories(related_task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_embeddings_model ON memory_embeddings(embedding_model)")

        _ensure_owner_column(conn)

        conn.commit()

    def _ensure_memory_tables(self):
        """memoryrelateddatabase"""
        with get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    importance TEXT NOT NULL,
                    keywords TEXT,
                    context TEXT DEFAULT 'General',
                    tags TEXT,
                    related_task_id INTEGER,
                    owner_id TEXT,
                    links TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    retrieval_count INTEGER DEFAULT 0,
                    evolution_history TEXT,
                    embedding_generated BOOLEAN DEFAULT FALSE,
                    embedding_model TEXT
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_embeddings (
                    memory_id TEXT PRIMARY KEY,
                    embedding_vector TEXT NOT NULL,
                    embedding_model TEXT DEFAULT 'embedding-2',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (memory_id) REFERENCES memories (id) ON DELETE CASCADE
                )
            """)

            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(memory_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_task_id ON memories(related_task_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_embeddings_model ON memory_embeddings(embedding_model)")

            _ensure_owner_column(conn)

            conn.commit()

    @staticmethod
    def session_belongs_to_owner(session_id: str | None, owner_id: str | None) -> bool:
        if not session_id or not owner_id:
            return False
        return resolve_owner_id_for_session(session_id) == str(owner_id)

    async def _embed_text(self, text: str) -> list[float] | None:
        try:
            vector = await asyncio.wait_for(
                asyncio.to_thread(self.embeddings_service.get_single_embedding, text),
                timeout=DEDUPE_EMBEDDING_TIMEOUT_S,
            )
        except Exception as exc:
            logger.debug("embedding unavailable: %s", exc)
            return None
        return vector or None

    def _find_duplicate(
        self,
        content: str,
        owner_id: str,
        session_id: str | None = None,
    ) -> sqlite3.Row | None:
        """Find an owner's existing memory whose normalized content is identical.

        Only exact normalized matches are merged. Semantic near-matches are kept
        as separate rows so an updated fact (e.g. a new version or path) is
        never silently dropped in favour of the stale one.
        """
        target = normalize_memory_content(content)
        if not target:
            return None
        with self._get_conn(session_id) as conn:
            conn.create_function("memory_norm", 1, normalize_memory_content, deterministic=True)
            return conn.execute(
                "SELECT * FROM memories WHERE owner_id = ? AND memory_norm(content) = ? "
                "ORDER BY created_at ASC, id LIMIT 1",
                (owner_id, target),
            ).fetchone()

    def _merge_into_existing(
        self,
        existing: sqlite3.Row,
        request: SaveMemoryRequest,
        session_id: str | None,
    ) -> tuple[list[str], list[str]]:
        keywords = _merge_unique(_json_str_list(existing["keywords"]), list(request.keywords or []))
        tags = _merge_unique(_json_str_list(existing["tags"]), list(request.tags or []))
        importance = _higher_importance(existing["importance"], request.importance.value)
        with self._get_conn(session_id) as conn:
            conn.execute(
                "UPDATE memories SET keywords = ?, tags = ?, importance = ?, "
                "last_accessed = CURRENT_TIMESTAMP WHERE id = ? AND owner_id = ?",
                (json.dumps(keywords), json.dumps(tags), importance, existing["id"], request.owner_id),
            )
            conn.commit()
        _invalidate_memory_cache()
        return keywords, tags

    async def save_memory(self, request: SaveMemoryRequest) -> SaveMemoryResponse:
        """Save memory entry with optional session-scoped storage."""
        if request.session_id and request.owner_id:
            if not self.session_belongs_to_owner(request.session_id, request.owner_id):
                raise MemoryAccessDenied("session does not belong to owner")
        try:
            if request.owner_id:
                existing = self._find_duplicate(request.content, request.owner_id, session_id=request.session_id)
                if existing is not None:
                    keywords, tags = self._merge_into_existing(existing, request, request.session_id)
                    return SaveMemoryResponse(
                        memory_id=existing["id"],
                        task_id=existing["related_task_id"],
                        memory_type=MemoryType(existing["memory_type"]),
                        content=existing["content"],
                        created_at=existing["created_at"],
                        embedding_generated=bool(existing["embedding_generated"]),
                        keywords=keywords,
                        context=existing["context"] or "General",
                        tags=tags,
                    )

            memory_id = str(uuid.uuid4())

            keywords = request.keywords or []
            context = request.context or "General"
            tags = request.tags or []

            if not keywords or context == "General" or not tags:
                analysis = await self._analyze_content(request.content)
                if not keywords:
                    keywords = analysis.get("keywords", [])
                if context == "General":
                    context = analysis.get("context", "General")
                if not tags:
                    tags = analysis.get("tags", [])

            memory_note = MemoryNote(
                id=memory_id,
                content=request.content,
                memory_type=request.memory_type,
                importance=request.importance,
                keywords=keywords,
                context=context,
                tags=tags,
                related_task_id=request.related_task_id,
                owner_id=request.owner_id,
                created_at=datetime.now(),
                last_accessed=datetime.now(),
            )

            await self._store_memory(memory_note, session_id=request.session_id)

            embedding_generated = await self._generate_embedding(memory_note, session_id=request.session_id)
            memory_note.embedding_generated = embedding_generated

            await self._process_memory_evolution(memory_note, session_id=request.session_id)
            _invalidate_memory_cache()

            return SaveMemoryResponse(
                memory_id=memory_id,
                task_id=request.related_task_id,
                memory_type=request.memory_type,
                content=request.content,
                created_at=memory_note.created_at,
                embedding_generated=embedding_generated,
                keywords=keywords,
                context=context,
                tags=tags,
            )

        except Exception as e:
            logger.error(f"Failed to save memory: {e}")
            raise

    async def query_memory(self, request: QueryMemoryRequest, *, track_retrieval: bool = True) -> QueryMemoryResponse:
        """memory(support session )"""
        try:
            start_time = datetime.now()

            where_conditions = []
            params = []

            if request.memory_types:
                type_placeholders = ",".join(["?" for _ in request.memory_types])
                where_conditions.append(f"m.memory_type IN ({type_placeholders})")
                params.extend([t.value for t in request.memory_types])

            # Fail-closed owner isolation for both global and per-session stores.
            if not request.owner_id:
                logger.warning("query_memory without owner_id; returning empty")
                return QueryMemoryResponse(memories=[], total=0, search_time_ms=0.0)
            if request.session_id:
                if not self.session_belongs_to_owner(request.session_id, request.owner_id):
                    logger.warning("query_memory denied: session %s not owned by caller", request.session_id)
                    return QueryMemoryResponse(memories=[], total=0, search_time_ms=0.0)
                # Legacy per-session rows may predate owner_id; the session itself is
                # verified as the caller's, so NULL-owner rows there are authorized.
                where_conditions.append("(m.owner_id = ? OR m.owner_id IS NULL)")
            else:
                where_conditions.append("m.owner_id = ?")
            params.append(request.owner_id)

            memories = await self._semantic_search(
                query=request.search_text,
                where_conditions=where_conditions,
                params=params,
                limit=request.limit,
                min_similarity=request.min_similarity,
                session_id=request.session_id,
            )
            if memories and track_retrieval:
                with self._get_conn(request.session_id, owner_id=request.owner_id, require_owner=True) as conn:
                    mark_memories_recalled(conn, [m["id"] for m in memories])

            memory_items = []
            for memory_data in memories:
                memory_items.append(
                    MemoryItem(
                        memory_id=memory_data["id"],
                        task_id=memory_data.get("related_task_id"),
                        memory_type=MemoryType(memory_data["memory_type"]),
                        content=memory_data["content"],
                        similarity=memory_data.get("similarity", 0.0),
                        created_at=memory_data["created_at"],
                        keywords=json.loads(memory_data.get("keywords", "[]")),
                        context=memory_data.get("context", "General"),
                        tags=json.loads(memory_data.get("tags", "[]")),
                        importance=ImportanceLevel(memory_data["importance"]),
                    )
                )

            search_time = (datetime.now() - start_time).total_seconds() * 1000

            return QueryMemoryResponse(memories=memory_items, total=len(memory_items), search_time_ms=search_time)

        except Exception as e:
            logger.error(f"Failed to query memory: {e}")
            raise

    async def _analyze_content(self, content: str) -> dict[str, Any]:
        """Use LLM to extract keywords, context, and tags from memory content."""
        try:
            prompt = f"""
Analyze the following content and extract key information:

Content:
{content}

Return the analysis result in JSON format:
{{
    "keywords": ["keyword1", "keyword2", "keyword3"],
    "context": "Main context or domain of the content",
    "tags": ["tag1", "tag2", "tag3"]
}}
"""

            # The LLM client is synchronous. Memory saves run as tasks on the chat event loop, so a call
            # made here directly would stall every stream and the run lease heartbeat for its whole duration.
            response = await asyncio.to_thread(self.llm_client.chat, prompt)

            if isinstance(response, dict):
                result_text = response.get("content", "")
            else:
                result_text = str(response)

            json_start = result_text.find("{")
            json_end = result_text.rfind("}") + 1

            if json_start >= 0 and json_end > json_start:
                json_text = result_text[json_start:json_end]
                analysis = json.loads(json_text)
                return analysis
            else:
                return self._fallback_analysis(content)

        except Exception as e:
            logger.warning(f"LLM content analysis failed: {e}")
            return self._fallback_analysis(content)

    def _fallback_analysis(self, content: str) -> dict[str, Any]:
        """contentanalysisfallback"""
        words = content.split()

        keywords = []
        for word in words[:10]:  # 10
            if len(word) > 2 and word.isalpha():
                keywords.append(word)

        if any(kw in content.lower() for kw in ["", "", "", ""]):
            context = ""
            tags = ["", "", ""]
        elif any(kw in content.lower() for kw in ["AI", "", "", ""]):
            context = ""
            tags = ["AI", "", ""]
        else:
            context = "content"
            tags = ["", "content"]

        return {"keywords": keywords[:5], "context": context, "tags": tags}

    async def _store_memory(self, memory_note: MemoryNote, session_id: str | None = None):
        """memorydatabase(support session )"""
        with self._get_conn(session_id) as conn:
            conn.execute(
                """
                INSERT INTO memories (
                    id, content, memory_type, importance, keywords, context, tags,
                    related_task_id, owner_id, links, created_at, last_accessed, retrieval_count,
                    evolution_history, embedding_generated, embedding_model
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    memory_note.id,
                    memory_note.content,
                    memory_note.memory_type.value,
                    memory_note.importance.value,
                    json.dumps(memory_note.keywords),
                    memory_note.context,
                    json.dumps(memory_note.tags),
                    memory_note.related_task_id,
                    memory_note.owner_id,
                    json.dumps(memory_note.links),
                    memory_note.created_at,
                    memory_note.last_accessed,
                    memory_note.retrieval_count,
                    json.dumps(memory_note.evolution_history),
                    memory_note.embedding_generated,
                    memory_note.embedding_model,
                ),
            )
            conn.commit()

    async def _generate_embedding(self, memory_note: MemoryNote, session_id: str | None = None) -> bool:
        """memory(support session )"""
        try:
            embedding_text = self._build_embedding_text(memory_note)

            embedding = await asyncio.to_thread(self.embeddings_service.get_single_embedding, embedding_text)

            if embedding:
                embedding_json = json.dumps(embedding)
                with self._get_conn(session_id) as conn:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO memory_embeddings
                        (memory_id, embedding_vector, embedding_model, updated_at)
                        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                        (memory_note.id, embedding_json, "embedding-2"),
                    )

                    conn.execute(
                        """
                        UPDATE memories SET embedding_generated = TRUE, embedding_model = ?
                        WHERE id = ?
                    """,
                        ("embedding-2", memory_note.id),
                    )

                    conn.commit()

                return True
            else:
                return False

        except Exception as e:
            logger.error(f"Failed to generate embedding for memory {memory_note.id}: {e}")
            return False

    def _build_embedding_text(self, memory_note: MemoryNote) -> str:
        """embedding"""
        parts = [memory_note.content]

        if memory_note.keywords:
            parts.append(f": {', '.join(memory_note.keywords)}")

        if memory_note.context and memory_note.context != "General":
            parts.append(f": {memory_note.context}")

        if memory_note.tags:
            parts.append(f": {', '.join(memory_note.tags)}")

        return " | ".join(parts)

    def _embedding_text_from_memory_row(self, row: sqlite3.Row) -> str:
        """Build the same embedding text used for a MemoryNote from a SQL row."""
        content = str(row["content"] or "")
        try:
            keywords = json.loads(row["keywords"] or "[]")
        except (json.JSONDecodeError, TypeError):
            keywords = []
        if not isinstance(keywords, list):
            keywords = []
        context = str(row["context"] or "General")
        try:
            tags = json.loads(row["tags"] or "[]")
        except (json.JSONDecodeError, TypeError):
            tags = []
        if not isinstance(tags, list):
            tags = []

        parts: list[str] = [content]
        if keywords:
            parts.append(f": {', '.join(str(k) for k in keywords)}")
        if context and context != "General":
            parts.append(f": {context}")
        if tags:
            parts.append(f": {', '.join(str(t) for t in tags)}")
        return " | ".join(parts)

    async def reembed_memory_embeddings(
        self,
        session_id: str | None = None,
        *,
        limit: int | None = None,
    ) -> dict[str, int]:
        """
        Recompute and store embeddings for all memories that already have a row in
        memory_embeddings, using the current embedding client configuration.

        Use after changing QWEN_EMBEDDING_MODEL / QWEN_EMBEDDING_DIM or switching away
        from a mixed local/API embedding history.
        """
        updated = 0
        skipped = 0
        errors = 0
        model_label = getattr(
            getattr(self.embeddings_service, "api_client", None),
            "model",
            None,
        )
        if not isinstance(model_label, str) or not model_label.strip():
            model_label = "reembedded"

        with self._get_conn(session_id) as conn:
            rows = conn.execute("""
                SELECT m.id, m.content, m.keywords, m.context, m.tags
                FROM memories m
                INNER JOIN memory_embeddings me ON m.id = me.memory_id
                WHERE m.embedding_generated = 1
                ORDER BY m.created_at ASC
                """).fetchall()

        for idx, row in enumerate(rows):
            if limit is not None and idx >= limit:
                break
            memory_id = row["id"]
            try:
                text = self._embedding_text_from_memory_row(row)
                embedding = await asyncio.to_thread(self.embeddings_service.get_single_embedding, text)
                if not embedding:
                    skipped += 1
                    continue
                embedding_json = json.dumps(embedding)
                with self._get_conn(session_id) as conn:
                    conn.execute(
                        """
                        UPDATE memory_embeddings
                        SET embedding_vector = ?, embedding_model = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE memory_id = ?
                        """,
                        (embedding_json, model_label, memory_id),
                    )
                    conn.execute(
                        """
                        UPDATE memories SET embedding_model = ? WHERE id = ?
                        """,
                        (model_label, memory_id),
                    )
                    conn.commit()
                updated += 1
            except Exception as exc:
                logger.warning("reembed failed for memory %s: %s", memory_id, exc)
                errors += 1

        logger.info(
            "reembed_memory_embeddings finished: updated=%s skipped=%s errors=%s session_id=%s",
            updated,
            skipped,
            errors,
            session_id,
        )
        return {"updated": updated, "skipped": skipped, "errors": errors}

    async def _semantic_search(
        self,
        query: str,
        where_conditions: list[str],
        params: list[Any],
        limit: int,
        min_similarity: float,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """searchmemory(support session )"""
        try:
            query_embedding = await asyncio.to_thread(self.embeddings_service.get_single_embedding, query)

            if not query_embedding:
                return await self._text_search(query, where_conditions, params, limit, session_id)

            where_clause = "WHERE m.embedding_generated = TRUE"
            if where_conditions:
                where_clause += " AND " + " AND ".join(where_conditions)

            with self._get_conn(session_id) as conn:
                query_sql = f"""
                    SELECT m.*, me.embedding_vector
                    FROM memories m
                    JOIN memory_embeddings me ON m.id = me.memory_id
                    {where_clause}
                    ORDER BY m.created_at DESC
                """

                rows = conn.execute(query_sql, params).fetchall()

            results = []
            skipped_mismatch = 0
            for row in rows:
                try:
                    embedding_vector = _coerce_memory_embedding_for_query(
                        query_embedding,
                        row["embedding_vector"],
                    )
                    if embedding_vector is None:
                        skipped_mismatch += 1
                        continue

                    similarity = self.embeddings_service.compute_similarity(query_embedding, embedding_vector)

                    if similarity >= min_similarity:
                        memory_data = {
                            "id": row["id"],
                            "content": row["content"],
                            "memory_type": row["memory_type"],
                            "importance": row["importance"],
                            "keywords": row["keywords"],
                            "context": row["context"],
                            "tags": row["tags"],
                            "related_task_id": row["related_task_id"],
                            "created_at": row["created_at"],
                            "similarity": similarity,
                        }
                        results.append(memory_data)

                except Exception as e:
                    logger.warning(f"Error processing memory row: {e}")
                    continue

            if skipped_mismatch:
                logger.debug(
                    "Semantic search skipped %d memory row(s) with missing or incompatible "
                    "embedding dimension (query_dim=%d). Re-run scripts/reembed_memory_embeddings.py "
                    "to align stored vectors with the current embedding client.",
                    skipped_mismatch,
                    len(query_embedding),
                )

            results.sort(key=lambda x: x["similarity"], reverse=True)
            return results[:limit]

        except Exception as e:
            logger.error(f"Semantic search failed: {e}")
            return await self._text_search(query, where_conditions, params, limit, session_id)

    async def _text_search(
        self, query: str, where_conditions: list[str], params: list[Any], limit: int, session_id: str | None = None
    ) -> list[dict[str, Any]]:
        """searchfallback(support session )"""
        where_clause = "WHERE m.content LIKE ?"
        search_params: list[Any] = [f"%{query}%"]

        if where_conditions:
            where_clause += " AND " + " AND ".join(where_conditions)
            search_params.extend(params)

        with self._get_conn(session_id) as conn:
            query_sql = f"""
                SELECT m.* FROM memories m
                {where_clause}
                ORDER BY m.created_at DESC
                LIMIT ?
            """
            search_params.append(limit)

            rows = conn.execute(query_sql, search_params).fetchall()

        results = []
        for row in rows:
            memory_data = {
                "id": row["id"],
                "content": row["content"],
                "memory_type": row["memory_type"],
                "importance": row["importance"],
                "keywords": row["keywords"],
                "context": row["context"],
                "tags": row["tags"],
                "related_task_id": row["related_task_id"],
                "created_at": row["created_at"],
                "similarity": 0.5,  # default
            }
            results.append(memory_data)

        return results

    async def _process_memory_evolution(self, memory_note: MemoryNote, session_id: str | None = None):
        """memory"""
        try:
            self.evolution_count += 1

            if self.evolution_count % self.evolution_threshold == 0:
                await self._evolve_memories(session_id)

            await self._find_memory_connections(memory_note, session_id)

        except Exception as e:
            logger.error(f"Memory evolution failed: {e}")

    async def _find_memory_connections(self, memory_note: MemoryNote, session_id: str | None = None):
        """memoryrelatedconnection"""
        try:
            query_request = QueryMemoryRequest(
                search_text=memory_note.content,
                limit=5,
                min_similarity=0.6,
                session_id=session_id,
                owner_id=memory_note.owner_id,
            )

            related_memories = await self.query_memory(query_request, track_retrieval=False)

            connections = []
            for related in related_memories.memories:
                if related.memory_id != memory_note.id and related.similarity > 0.7:
                    connections.append(related.memory_id)

            if connections:
                memory_note.links.extend(connections[:3])  # 3connection
                await self._update_memory_links(memory_note.id, memory_note.links, session_id)

        except Exception as e:
            logger.error(f"Failed to find memory connections: {e}")

    async def _update_memory_links(self, memory_id: str, links: list[str], session_id: str | None = None):
        """updatememoryconnection"""
        with self._get_conn(session_id) as conn:
            conn.execute(
                """
                UPDATE memories SET links = ? WHERE id = ?
            """,
                (json.dumps(links), memory_id),
            )
            conn.commit()

    async def _evolve_memories(self, session_id: str | None = None):
        """executememory"""
        try:
            logger.info("Starting memory evolution process...")

            with self._get_conn(session_id) as conn:
                rows = conn.execute("""
                    SELECT * FROM memories
                    ORDER BY created_at DESC
                    LIMIT 20
                """).fetchall()

            for row in rows:
                try:
                    await self._evolve_single_memory(row)
                except Exception as e:
                    logger.warning(f"Failed to evolve memory {row['id']}: {e}")

            logger.info("Memory evolution process completed")

        except Exception as e:
            logger.error(f"Memory evolution failed: {e}")

    async def _evolve_single_memory(self, memory_row):
        """memory"""
        pass

    def delete_memories_for_session(self, session_id: str, owner_id: str) -> int:
        """Delete the authorized owner's memories tagged exactly ``session:<id>``.

        Session-delete cascade. The caller must have authorized ``owner_id`` for
        ``session_id`` (e.g. the route deleted the ``chat_sessions`` row with an
        ``owner_id`` predicate) before calling. Deletion is scoped twice:

        * ``memories.owner_id`` must equal ``owner_id`` — rows of other owners
          that happen to carry the same tag are never touched;
        * the JSON ``tags`` array must contain the exact element
          ``session:<id>`` — no ``LIKE`` matching, so ``_``/``%`` in a session
          id or a session id that is a prefix of another cannot widen the match.

        Limits (old data is preserved, never guessed at): rows with a NULL
        ``owner_id`` (written before owner isolation) or with non-JSON ``tags``
        are left in place; they stay invisible to recall and are handled by
        ``scripts/memory_owner_backfill.py``. The per-session memory database
        file is removed only for an authorized call. Missing ``session_id`` or
        ``owner_id`` is a no-op. Returns the number of deleted memories.
        """
        if not session_id or not owner_id:
            return 0
        session_tag = f"{SESSION_TAG_PREFIX}{session_id}"
        params = (str(owner_id), session_tag)
        with get_db() as conn:
            conn.execute(
                """
                DELETE FROM memory_embeddings WHERE memory_id IN (
                    SELECT memories.id FROM memories
                    WHERE memories.owner_id = ?
                      AND EXISTS (
                        SELECT 1 FROM json_each(
                            CASE WHEN json_valid(memories.tags) THEN
                                CASE WHEN json_type(memories.tags) = 'array'
                                     THEN memories.tags ELSE '[]' END
                            ELSE '[]' END
                        ) AS t
                        WHERE t.type = 'text' AND t.value = ?
                      )
                )
                """,
                params,
            )
            cursor = conn.execute(
                """
                DELETE FROM memories
                WHERE memories.owner_id = ?
                  AND EXISTS (
                    SELECT 1 FROM json_each(
                        CASE WHEN json_valid(memories.tags) THEN
                            CASE WHEN json_type(memories.tags) = 'array'
                                 THEN memories.tags ELSE '[]' END
                        ELSE '[]' END
                    ) AS t
                    WHERE t.type = 'text' AND t.value = ?
                  )
                """,
                params,
            )
            deleted = max(int(cursor.rowcount or 0), 0)
            conn.commit()
        try:
            db_path = self.db_config.get_session_db_path(session_id)
            if db_path.exists():
                db_path.unlink()
        except OSError as exc:
            logger.warning("Failed to remove session memory db for %s: %s", session_id, exc)
        if deleted:
            logger.info("Deleted %s memories for session %s", deleted, session_id)
        return deleted

    # ------------------------------------------------------------------
    # Owner-scoped CRUD (user-facing memory governance)
    # ------------------------------------------------------------------

    def list_memories(
        self,
        owner_id: str,
        *,
        search: str | None = None,
        memory_type: str | None = None,
        importance: str | None = None,
        session_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit or 50), 200))
        offset = max(0, int(offset or 0))
        empty: dict[str, Any] = {"items": [], "total": 0, "limit": limit, "offset": offset}
        if not owner_id:
            return empty
        conditions = ["m.owner_id = ?"]
        params: list[Any] = [owner_id]
        if search and search.strip():
            like = f"%{search.strip()}%"
            conditions.append("(m.content LIKE ? OR m.keywords LIKE ? OR m.tags LIKE ?)")
            params.extend([like, like, like])
        if memory_type:
            conditions.append("m.memory_type = ?")
            params.append(memory_type)
        if importance:
            conditions.append("m.importance = ?")
            params.append(importance)
        if session_id:
            if not self.session_belongs_to_owner(session_id, owner_id):
                return empty
            conditions.append("m.tags LIKE ?")
            params.append(f'%"session:{session_id}"%')
        where = " AND ".join(conditions)
        with self._get_conn() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM memories m WHERE {where}", tuple(params)).fetchone()[0]
            rows = conn.execute(
                f"SELECT m.* FROM memories m WHERE {where} " "ORDER BY m.created_at DESC, m.id LIMIT ? OFFSET ?",
                tuple(params) + (limit, offset),
            ).fetchall()
        return {
            "items": [_memory_row_to_item(r) for r in rows],
            "total": int(total or 0),
            "limit": limit,
            "offset": offset,
        }

    def _get_owned_row(self, memory_id: str, owner_id: str) -> sqlite3.Row | None:
        if not memory_id or not owner_id:
            return None
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT m.* FROM memories m WHERE m.id = ? AND m.owner_id = ?",
                (memory_id, owner_id),
            ).fetchone()

    async def update_memory(
        self,
        memory_id: str,
        owner_id: str,
        *,
        content: str | None = None,
        importance: str | None = None,
        memory_type: str | None = None,
        tags: list[str] | None = None,
        keywords: list[str] | None = None,
    ) -> dict[str, Any] | None:
        row = self._get_owned_row(memory_id, owner_id)
        if row is None:
            return None
        sets: list[str] = []
        params: list[Any] = []
        content_changed = False
        if content is not None:
            text = str(content).strip()
            if not text:
                raise ValueError("content must not be empty")
            if text != row["content"]:
                content_changed = True
            sets.append("content = ?")
            params.append(text)
        if importance is not None:
            sets.append("importance = ?")
            params.append(ImportanceLevel(importance).value)
        if memory_type is not None:
            sets.append("memory_type = ?")
            params.append(MemoryType(memory_type).value)
        if tags is not None:
            sets.append("tags = ?")
            params.append(json.dumps(_merge_unique(list(tags))))
        if keywords is not None:
            sets.append("keywords = ?")
            params.append(json.dumps(_merge_unique(list(keywords))))
        if sets:
            sets.append("last_accessed = CURRENT_TIMESTAMP")
            with self._get_conn() as conn:
                conn.execute(
                    f"UPDATE memories SET {', '.join(sets)} WHERE id = ? AND owner_id = ?",
                    tuple(params) + (memory_id, owner_id),
                )
                conn.commit()
        updated = self._get_owned_row(memory_id, owner_id)
        if updated is None:
            return None
        if content_changed or (tags is not None) or (keywords is not None):
            await self._refresh_embedding_for_row(updated)
            updated = self._get_owned_row(memory_id, owner_id) or updated
        _invalidate_memory_cache()
        return _memory_row_to_item(updated)

    async def _refresh_embedding_for_row(self, row: sqlite3.Row) -> None:
        """Best-effort re-embed; on failure drop the stale vector."""
        vector = await self._embed_text(self._embedding_text_from_memory_row(row))
        with self._get_conn() as conn:
            if vector:
                conn.execute(
                    "INSERT OR REPLACE INTO memory_embeddings "
                    "(memory_id, embedding_vector, embedding_model, updated_at) "
                    "VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                    (row["id"], json.dumps(vector), "embedding-2"),
                )
                conn.execute(
                    "UPDATE memories SET embedding_generated = TRUE, embedding_model = ? WHERE id = ?",
                    ("embedding-2", row["id"]),
                )
            else:
                conn.execute("DELETE FROM memory_embeddings WHERE memory_id = ?", (row["id"],))
                conn.execute("UPDATE memories SET embedding_generated = FALSE WHERE id = ?", (row["id"],))
            conn.commit()

    def delete_memory(self, memory_id: str, owner_id: str) -> bool:
        if not memory_id or not owner_id:
            return False
        with self._get_conn() as conn:
            hit = conn.execute("SELECT 1 FROM memories WHERE id = ? AND owner_id = ?", (memory_id, owner_id)).fetchone()
            if not hit:
                return False
            conn.execute("DELETE FROM memory_embeddings WHERE memory_id = ?", (memory_id,))
            cursor = conn.execute("DELETE FROM memories WHERE id = ? AND owner_id = ?", (memory_id, owner_id))
            conn.commit()
            deleted = cursor.rowcount > 0
        if deleted:
            _invalidate_memory_cache()
        return deleted

    async def get_memory_stats(self, owner_id: str | None = None) -> MemoryStats:
        """Memory statistics; scoped to ``owner_id`` when given."""
        scope = "WHERE owner_id = ?" if owner_id else ""
        scope_and = "AND owner_id = ?" if owner_id else ""
        sp: tuple[Any, ...] = (owner_id,) if owner_id else ()
        with get_db() as conn:
            total_memories = conn.execute(f"SELECT COUNT(*) FROM memories {scope}", sp).fetchone()[0]

            type_stats = conn.execute(
                f"SELECT memory_type, COUNT(*) as count FROM memories {scope} GROUP BY memory_type",
                sp,
            ).fetchall()

            importance_stats = conn.execute(
                f"SELECT importance, COUNT(*) as count FROM memories {scope} GROUP BY importance",
                sp,
            ).fetchall()

            embedding_count = conn.execute(
                f"SELECT COUNT(*) FROM memories WHERE embedding_generated = TRUE {scope_and}", sp
            ).fetchone()[0]

            avg_connections = (
                conn.execute(
                    "SELECT AVG(json_array_length(links)) as avg_links FROM memories "
                    f"WHERE links IS NOT NULL AND links != '[]' {scope_and}",
                    sp,
                ).fetchone()[0]
                or 0.0
            )

        return MemoryStats(
            total_memories=total_memories,
            memory_type_distribution={row[0]: row[1] for row in type_stats},
            importance_distribution={row[0]: row[1] for row in importance_stats},
            average_connections=avg_connections,
            embedding_coverage=embedding_count / total_memories if total_memories > 0 else 0.0,
            evolution_count=self.evolution_count,
        )


_memory_service = None


def get_memory_service() -> IntegratedMemoryService:
    """getmemoryservice"""
    global _memory_service
    if _memory_service is None:
        _memory_service = IntegratedMemoryService()
    return _memory_service
