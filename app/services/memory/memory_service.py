"""
Integrated Memory Service.

Combines Memory-MCP capabilities with existing system infrastructure
"""

import json
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

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
    query_embedding: List[float],
    raw_vector: Any,
) -> Optional[List[float]]:
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


class IntegratedMemoryService:
    """memoryservice - , support session """

    def __init__(self):
        self.llm_client = get_default_client()
        self.embeddings_service = get_embeddings_service()
        self.evolution_threshold = 10  # 10memory
        self.evolution_count = 0
        self.db_config = get_database_config()

        self._ensure_memory_tables()

    @contextmanager
    def _get_conn(self, session_id: Optional[str] = None) -> Generator[sqlite3.Connection, None, None]:
        """
        session_id  session .

        Args:
            session_id: session, None 

        Yields:
            databaseconnection
        """
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                importance TEXT NOT NULL,
                keywords TEXT,
                context TEXT DEFAULT 'General',
                tags TEXT,
                related_task_id INTEGER,
                links TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                retrieval_count INTEGER DEFAULT 0,
                evolution_history TEXT,
                embedding_generated BOOLEAN DEFAULT FALSE,
                embedding_model TEXT
            )
        """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_embeddings (
                memory_id TEXT PRIMARY KEY,
                embedding_vector TEXT NOT NULL,
                embedding_model TEXT DEFAULT 'embedding-2',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (memory_id) REFERENCES memories (id) ON DELETE CASCADE
            )
        """
        )

        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(memory_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_task_id ON memories(related_task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_embeddings_model ON memory_embeddings(embedding_model)")

        conn.commit()

    def _ensure_memory_tables(self):
        """memoryrelateddatabase"""
        with get_db() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    importance TEXT NOT NULL,
                    keywords TEXT,
                    context TEXT DEFAULT 'General',
                    tags TEXT,
                    related_task_id INTEGER,
                    links TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    retrieval_count INTEGER DEFAULT 0,
                    evolution_history TEXT,
                    embedding_generated BOOLEAN DEFAULT FALSE,
                    embedding_model TEXT
                )
            """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_embeddings (
                    memory_id TEXT PRIMARY KEY,
                    embedding_vector TEXT NOT NULL,
                    embedding_model TEXT DEFAULT 'embedding-2',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (memory_id) REFERENCES memories (id) ON DELETE CASCADE
                )
            """
            )

            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(memory_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_task_id ON memories(related_task_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_embeddings_model ON memory_embeddings(embedding_model)")

            conn.commit()

    async def save_memory(self, request: SaveMemoryRequest) -> SaveMemoryResponse:
        """Save memory entry with optional session-scoped storage."""
        try:
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
                created_at=datetime.now(),
                last_accessed=datetime.now(),
            )

            await self._store_memory(memory_note, session_id=request.session_id)

            embedding_generated = await self._generate_embedding(
                memory_note, session_id=request.session_id
            )
            memory_note.embedding_generated = embedding_generated

            await self._process_memory_evolution(memory_note, session_id=request.session_id)

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

    async def query_memory(self, request: QueryMemoryRequest) -> QueryMemoryResponse:
        """memory(support session )"""
        try:
            start_time = datetime.now()

            where_conditions = []
            params = []

            if request.memory_types:
                type_placeholders = ",".join(["?" for _ in request.memory_types])
                where_conditions.append(f"memory_type IN ({type_placeholders})")
                params.extend([t.value for t in request.memory_types])

            memories = await self._semantic_search(
                query=request.search_text,
                where_conditions=where_conditions,
                params=params,
                limit=request.limit,
                min_similarity=request.min_similarity,
                session_id=request.session_id,
            )

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

    async def _analyze_content(self, content: str) -> Dict[str, Any]:
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

            response = self.llm_client.chat(prompt)

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

    def _fallback_analysis(self, content: str) -> Dict[str, Any]:
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

    async def _store_memory(self, memory_note: MemoryNote, session_id: Optional[str] = None):
        """memorydatabase(support session )"""
        with self._get_conn(session_id) as conn:
            conn.execute(
                """
                INSERT INTO memories (
                    id, content, memory_type, importance, keywords, context, tags,
                    related_task_id, links, created_at, last_accessed, retrieval_count,
                    evolution_history, embedding_generated, embedding_model
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    async def _generate_embedding(self, memory_note: MemoryNote, session_id: Optional[str] = None) -> bool:
        """memory(support session )"""
        try:
            embedding_text = self._build_embedding_text(memory_note)

            embedding = self.embeddings_service.get_single_embedding(embedding_text)

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

        parts: List[str] = [content]
        if keywords:
            parts.append(f": {', '.join(str(k) for k in keywords)}")
        if context and context != "General":
            parts.append(f": {context}")
        if tags:
            parts.append(f": {', '.join(str(t) for t in tags)}")
        return " | ".join(parts)

    async def reembed_memory_embeddings(
        self,
        session_id: Optional[str] = None,
        *,
        limit: Optional[int] = None,
    ) -> Dict[str, int]:
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
            rows = conn.execute(
                """
                SELECT m.id, m.content, m.keywords, m.context, m.tags
                FROM memories m
                INNER JOIN memory_embeddings me ON m.id = me.memory_id
                WHERE m.embedding_generated = 1
                ORDER BY m.created_at ASC
                """
            ).fetchall()

        for idx, row in enumerate(rows):
            if limit is not None and idx >= limit:
                break
            memory_id = row["id"]
            try:
                text = self._embedding_text_from_memory_row(row)
                embedding = self.embeddings_service.get_single_embedding(text)
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
        self, query: str, where_conditions: List[str], params: List[Any], limit: int, min_similarity: float,
        session_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """searchmemory(support session )"""
        try:
            query_embedding = self.embeddings_service.get_single_embedding(query)

            if not query_embedding:
                return await self._text_search(query, where_conditions, params, limit, session_id)

            where_clause = "WHERE embedding_generated = TRUE"
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

                    similarity = self.embeddings_service.compute_similarity(
                        query_embedding, embedding_vector
                    )

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
        self, query: str, where_conditions: List[str], params: List[Any], limit: int,
        session_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """searchfallback(support session )"""
        where_clause = "WHERE content LIKE ?"
        search_params = [f"%{query}%"]

        if where_conditions:
            where_clause += " AND " + " AND ".join(where_conditions)
            search_params.extend(params)

        with self._get_conn(session_id) as conn:
            query_sql = f"""
                SELECT * FROM memories
                {where_clause}
                ORDER BY created_at DESC
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

    async def _process_memory_evolution(self, memory_note: MemoryNote, session_id: Optional[str] = None):
        """memory"""
        try:
            self.evolution_count += 1

            if self.evolution_count % self.evolution_threshold == 0:
                await self._evolve_memories(session_id)

            await self._find_memory_connections(memory_note, session_id)

        except Exception as e:
            logger.error(f"Memory evolution failed: {e}")

    async def _find_memory_connections(self, memory_note: MemoryNote, session_id: Optional[str] = None):
        """memoryrelatedconnection"""
        try:
            query_request = QueryMemoryRequest(
                search_text=memory_note.content,
                limit=5,
                min_similarity=0.6,
                session_id=session_id
            )

            related_memories = await self.query_memory(query_request)

            connections = []
            for related in related_memories.memories:
                if related.memory_id != memory_note.id and related.similarity > 0.7:
                    connections.append(related.memory_id)

            if connections:
                memory_note.links.extend(connections[:3])  # 3connection
                await self._update_memory_links(memory_note.id, memory_note.links, session_id)

        except Exception as e:
            logger.error(f"Failed to find memory connections: {e}")

    async def _update_memory_links(self, memory_id: str, links: List[str], session_id: Optional[str] = None):
        """updatememoryconnection"""
        with self._get_conn(session_id) as conn:
            conn.execute(
                """
                UPDATE memories SET links = ? WHERE id = ?
            """,
                (json.dumps(links), memory_id),
            )
            conn.commit()

    async def _evolve_memories(self, session_id: Optional[str] = None):
        """executememory"""
        try:
            logger.info("Starting memory evolution process...")

            with self._get_conn(session_id) as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM memories
                    ORDER BY created_at DESC
                    LIMIT 20
                """
                ).fetchall()

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

    async def get_memory_stats(self) -> MemoryStats:
        """getmemorysystemstatistics"""
        with get_db() as conn:
            total_memories = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

            type_stats = conn.execute(
                """
                SELECT memory_type, COUNT(*) as count
                FROM memories
                GROUP BY memory_type
            """
            ).fetchall()

            importance_stats = conn.execute(
                """
                SELECT importance, COUNT(*) as count
                FROM memories
                GROUP BY importance
            """
            ).fetchall()

            embedding_count = conn.execute(
                """
                SELECT COUNT(*) FROM memories WHERE embedding_generated = TRUE
            """
            ).fetchone()[0]

            avg_connections = (
                conn.execute(
                    """
                SELECT AVG(json_array_length(links)) as avg_links
                FROM memories
                WHERE links IS NOT NULL AND links != '[]'
            """
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
