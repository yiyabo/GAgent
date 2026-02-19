#!/usr/bin/env python3
"""


, 
, high. 
"""

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.services.foundation.config import get_config
from app.services.foundation.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass
class ThreadSafeCacheEntry:
    """"""

    text_hash: str
    embedding: List[float]
    model: str
    created_at: float
    access_count: int = 0
    last_accessed: float = 0.0
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def update_access_stats(self, current_time: float) -> None:
        """update"""
        with self._lock:
            self.access_count += 1
            self.last_accessed = current_time

    def get_embedding_copy(self) -> List[float]:
        """getembedding"""
        with self._lock:
            return self.embedding.copy()


class ThreadSafeEmbeddingCache:
    """"""

    def __init__(self, cache_size: int = 10000, enable_persistent: bool = True):
        self.config = get_config()
        self.cache_size = cache_size
        self.enable_persistent = enable_persistent

        self._memory_cache: Dict[str, ThreadSafeCacheEntry] = {}
        self._cache_lock = RWLock()

        self._db_lock = threading.RLock()
        self._db_connections: Dict[int, sqlite3.Connection] = {}

        from ...config.database_config import get_cache_database_path
        self.cache_db_path = get_cache_database_path("embedding")

        if self.enable_persistent:
            self._init_persistent_cache()

        logger.info(
            f"Thread-safe embedding cache initialized: memory_size={cache_size}, persistent={enable_persistent}"
        )

    def _init_persistent_cache(self):
        """database"""
        try:
            with self._get_db_connection() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS embedding_cache (
                        text_hash TEXT PRIMARY KEY,
                        embedding_json TEXT NOT NULL,
                        model TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        access_count INTEGER DEFAULT 0,
                        last_accessed REAL DEFAULT 0.0
                    )
                """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_model ON embedding_cache(model)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_last_accessed ON embedding_cache(last_accessed)")
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to initialize persistent cache: {e}")
            self.enable_persistent = False

    @contextmanager
    def _get_db_connection(self):
        """getdatabaseconnection"""
        thread_id = threading.get_ident()

        with self._db_lock:
            if thread_id not in self._db_connections:
                self._db_connections[thread_id] = sqlite3.connect(
                    self.cache_db_path, timeout=30.0, check_same_thread=False
                )

        conn = self._db_connections[thread_id]
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        finally:
            pass

    def _compute_text_hash(self, text: str, model: str) -> str:
        """model"""
        content = f"{model}:{text}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def get(self, text: str, model: str = None) -> Optional[List[float]]:
        """get"""
        if not text.strip():
            return None

        model = model or self.config.embedding_model
        text_hash = self._compute_text_hash(text, model)
        current_time = time.time()

        with self._cache_lock.read_lock():
            if text_hash in self._memory_cache:
                entry = self._memory_cache[text_hash]
                entry.update_access_stats(current_time)
                logger.debug(f"Cache hit (memory): {text_hash[:8]}...")
                return entry.get_embedding_copy()

        if self.enable_persistent:
            embedding = self._get_from_persistent_cache(text_hash, model, current_time)
            if embedding is not None:
                return embedding

        logger.debug(f"Cache miss: {text_hash[:8]}...")
        return None

    def _get_from_persistent_cache(self, text_hash: str, model: str, current_time: float) -> Optional[List[float]]:
        """get"""
        try:
            with self._get_db_connection() as conn:
                row = conn.execute(
                    "SELECT embedding_json, access_count FROM embedding_cache WHERE text_hash = ? AND model = ?",
                    (text_hash, model),
                ).fetchone()

                if row:
                    embedding = json.loads(row[0])
                    access_count = row[1] + 1

                    conn.execute(
                        "UPDATE embedding_cache SET access_count = ?, last_accessed = ? WHERE text_hash = ?",
                        (access_count, current_time, text_hash),
                    )
                    conn.commit()

                    self._load_to_memory_cache(
                        ThreadSafeCacheEntry(
                            text_hash=text_hash,
                            embedding=embedding,
                            model=model,
                            created_at=current_time,
                            access_count=access_count,
                            last_accessed=current_time,
                        )
                    )

                    logger.debug(f"Cache hit (persistent): {text_hash[:8]}...")
                    return embedding.copy()
        except Exception as e:
            logger.warning(f"Failed to read from persistent cache: {e}")

        return None

    def put(self, text: str, embedding: List[float], model: str = None) -> None:
        """"""
        if not text.strip() or not embedding:
            return

        model = model or self.config.embedding_model
        text_hash = self._compute_text_hash(text, model)
        current_time = time.time()

        entry = ThreadSafeCacheEntry(
            text_hash=text_hash,
            embedding=embedding.copy(),
            model=model,
            created_at=current_time,
            access_count=1,
            last_accessed=current_time,
        )

        with self._cache_lock.write_lock():
            self._add_to_memory_cache_unsafe(entry)

        if self.enable_persistent:
            self._save_to_persistent_cache(entry)

        logger.debug(f"Cache stored: {text_hash[:8]}...")

    def _load_to_memory_cache(self, entry: ThreadSafeCacheEntry) -> None:
        """load()"""
        with self._cache_lock.write_lock():
            self._add_to_memory_cache_unsafe(entry)

    def _add_to_memory_cache_unsafe(self, entry: ThreadSafeCacheEntry) -> None:
        """(, )"""
        if len(self._memory_cache) >= self.cache_size:
            self._evict_lru_unsafe()

        self._memory_cache[entry.text_hash] = entry

    def _evict_lru_unsafe(self) -> None:
        """()"""
        if not self._memory_cache:
            return

        lru_key = min(
            self._memory_cache.keys(),
            key=lambda k: (self._memory_cache[k].access_count, self._memory_cache[k].last_accessed),
        )

        del self._memory_cache[lru_key]
        logger.debug(f"Evicted from memory cache: {lru_key[:8]}...")

    def _save_to_persistent_cache(self, entry: ThreadSafeCacheEntry) -> None:
        """save"""
        try:
            with self._get_db_connection() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO embedding_cache 
                    (text_hash, embedding_json, model, created_at, access_count, last_accessed)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (
                        entry.text_hash,
                        json.dumps(entry.embedding),
                        entry.model,
                        entry.created_at,
                        entry.access_count,
                        entry.last_accessed,
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.warning(f"Failed to write to persistent cache: {e}")

    def get_batch(self, texts: List[str], model: str = None) -> Tuple[List[Optional[List[float]]], List[int]]:
        """get()"""
        model = model or self.config.embedding_model
        results = []
        cache_misses = []

        for i, text in enumerate(texts):
            embedding = self.get(text, model)
            results.append(embedding)
            if embedding is None:
                cache_misses.append(i)

        return results, cache_misses

    def put_batch(self, texts: List[str], embeddings: List[List[float]], model: str = None) -> None:
        """()"""
        if len(texts) != len(embeddings):
            raise ValueError("texts and embeddings must have the same length")

        for text, embedding in zip(texts, embeddings):
            self.put(text, embedding, model)

    def get_stats(self) -> Dict[str, Any]:
        """getstatistics()"""
        with self._cache_lock.read_lock():
            stats = {
                "memory_cache_size": len(self._memory_cache),
                "memory_cache_limit": self.cache_size,
                "persistent_enabled": self.enable_persistent,
            }

        if self.enable_persistent:
            try:
                with self._get_db_connection() as conn:
                    row = conn.execute("SELECT COUNT(*) FROM embedding_cache").fetchone()
                    stats["persistent_cache_size"] = row[0] if row else 0

                    model_rows = conn.execute("SELECT model, COUNT(*) FROM embedding_cache GROUP BY model").fetchall()
                    stats["model_distribution"] = {row[0]: row[1] for row in model_rows}
            except Exception as e:
                logger.warning(f"Failed to get persistent cache stats: {e}")
                stats["persistent_cache_size"] = 0
                stats["model_distribution"] = {}

        return stats

    def clear_memory(self) -> None:
        """()"""
        with self._cache_lock.write_lock():
            self._memory_cache.clear()
        logger.info("Memory cache cleared")

    def shutdown(self) -> None:
        """close"""
        with self._db_lock:
            for conn in self._db_connections.values():
                try:
                    conn.close()
                except Exception:
                    pass
            self._db_connections.clear()
        logger.info("Thread-safe embedding cache shutdown completed")


class RWLock:
    """"""

    def __init__(self):
        self._read_ready = threading.Condition(threading.RLock())
        self._readers = 0

    @contextmanager
    def read_lock(self):
        """get"""
        with self._read_ready:
            self._readers += 1
        try:
            yield
        finally:
            with self._read_ready:
                self._readers -= 1
                if self._readers == 0:
                    self._read_ready.notify_all()

    @contextmanager
    def write_lock(self):
        """get"""
        with self._read_ready:
            while self._readers > 0:
                self._read_ready.wait()
            yield




_thread_safe_cache: Optional[ThreadSafeEmbeddingCache] = None
_cache_creation_lock = threading.Lock()


def get_thread_safe_embedding_cache() -> ThreadSafeEmbeddingCache:
    """get()"""
    global _thread_safe_cache

    if _thread_safe_cache is None:
        with _cache_creation_lock:
            if _thread_safe_cache is None:  # 
                settings = get_settings()
                cache_size = int(getattr(settings, "embedding_cache_size", 10000))
                enable_persistent = bool(getattr(settings, "embedding_cache_persistent", True))
                _thread_safe_cache = ThreadSafeEmbeddingCache(cache_size, enable_persistent)

    return _thread_safe_cache
