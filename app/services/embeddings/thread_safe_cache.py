#!/usr/bin/env python3
"""
线程安全的嵌入向量缓存管理模块

专门解决并发环境下的缓存读写竞态条件问题，使用细粒度锁和原子操作
确保线程安全，同时保持高性能。
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
    """线程安全的缓存条目"""

    text_hash: str
    embedding: List[float]
    model: str
    created_at: float
    access_count: int = 0
    last_accessed: float = 0.0
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def update_access_stats(self, current_time: float) -> None:
        """原子更新访问统计"""
        with self._lock:
            self.access_count += 1
            self.last_accessed = current_time

    def get_embedding_copy(self) -> List[float]:
        """获取embedding的线程安全副本"""
        with self._lock:
            return self.embedding.copy()


class ThreadSafeEmbeddingCache:
    """线程安全的嵌入向量缓存管理器"""

    def __init__(self, cache_size: int = 10000, enable_persistent: bool = True):
        self.config = get_config()
        self.cache_size = cache_size
        self.enable_persistent = enable_persistent

        # 使用读写锁来优化并发性能（使用本模块的 RWLock 实现，避免全局猴子补丁）
        self._memory_cache: Dict[str, ThreadSafeCacheEntry] = {}
        self._cache_lock = RWLock()

        # 数据库连接池锁
        self._db_lock = threading.RLock()
        self._db_connections: Dict[int, sqlite3.Connection] = {}

        # 持久化缓存路径 - 使用规范的缓存目录
        from ...config.database_config import get_cache_database_path
        self.cache_db_path = get_cache_database_path("embedding")

        if self.enable_persistent:
            self._init_persistent_cache()

        logger.info(
            f"Thread-safe embedding cache initialized: memory_size={cache_size}, persistent={enable_persistent}"
        )

    def _init_persistent_cache(self):
        """初始化持久化缓存数据库"""
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
        """获取线程安全的数据库连接"""
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
            # 连接保持在池中，不关闭
            pass

    def _compute_text_hash(self, text: str, model: str) -> str:
        """计算文本和模型的哈希值"""
        content = f"{model}:{text}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def get(self, text: str, model: str = None) -> Optional[List[float]]:
        """线程安全获取嵌入向量"""
        if not text.strip():
            return None

        model = model or self.config.embedding_model
        text_hash = self._compute_text_hash(text, model)
        current_time = time.time()

        # 1. 先检查内存缓存（使用读锁）
        with self._cache_lock.read_lock():
            if text_hash in self._memory_cache:
                entry = self._memory_cache[text_hash]
                entry.update_access_stats(current_time)
                logger.debug(f"Cache hit (memory): {text_hash[:8]}...")
                return entry.get_embedding_copy()

        # 2. 检查持久化缓存
        if self.enable_persistent:
            embedding = self._get_from_persistent_cache(text_hash, model, current_time)
            if embedding is not None:
                return embedding

        logger.debug(f"Cache miss: {text_hash[:8]}...")
        return None

    def _get_from_persistent_cache(self, text_hash: str, model: str, current_time: float) -> Optional[List[float]]:
        """从持久化缓存获取嵌入向量"""
        try:
            with self._get_db_connection() as conn:
                row = conn.execute(
                    "SELECT embedding_json, access_count FROM embedding_cache WHERE text_hash = ? AND model = ?",
                    (text_hash, model),
                ).fetchone()

                if row:
                    embedding = json.loads(row[0])
                    access_count = row[1] + 1

                    # 更新访问统计
                    conn.execute(
                        "UPDATE embedding_cache SET access_count = ?, last_accessed = ? WHERE text_hash = ?",
                        (access_count, current_time, text_hash),
                    )
                    conn.commit()

                    # 加载到内存缓存
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
        """线程安全存储嵌入向量"""
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

        # 存储到内存缓存（使用写锁）
        with self._cache_lock.write_lock():
            self._add_to_memory_cache_unsafe(entry)

        # 存储到持久化缓存
        if self.enable_persistent:
            self._save_to_persistent_cache(entry)

        logger.debug(f"Cache stored: {text_hash[:8]}...")

    def _load_to_memory_cache(self, entry: ThreadSafeCacheEntry) -> None:
        """加载条目到内存缓存（线程安全）"""
        with self._cache_lock.write_lock():
            self._add_to_memory_cache_unsafe(entry)

    def _add_to_memory_cache_unsafe(self, entry: ThreadSafeCacheEntry) -> None:
        """添加条目到内存缓存（非线程安全，需要在锁保护下调用）"""
        # 检查容量并进行LRU淘汰
        if len(self._memory_cache) >= self.cache_size:
            self._evict_lru_unsafe()

        self._memory_cache[entry.text_hash] = entry

    def _evict_lru_unsafe(self) -> None:
        """移除最近最少使用的缓存条目（非线程安全）"""
        if not self._memory_cache:
            return

        # 找到最少使用和最近最少访问的条目
        lru_key = min(
            self._memory_cache.keys(),
            key=lambda k: (self._memory_cache[k].access_count, self._memory_cache[k].last_accessed),
        )

        del self._memory_cache[lru_key]
        logger.debug(f"Evicted from memory cache: {lru_key[:8]}...")

    def _save_to_persistent_cache(self, entry: ThreadSafeCacheEntry) -> None:
        """保存到持久化缓存"""
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
        """批量获取嵌入向量（线程安全）"""
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
        """批量存储嵌入向量（线程安全）"""
        if len(texts) != len(embeddings):
            raise ValueError("texts and embeddings must have the same length")

        for text, embedding in zip(texts, embeddings):
            self.put(text, embedding, model)

    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息（线程安全）"""
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

                    # 获取模型分布
                    model_rows = conn.execute("SELECT model, COUNT(*) FROM embedding_cache GROUP BY model").fetchall()
                    stats["model_distribution"] = {row[0]: row[1] for row in model_rows}
            except Exception as e:
                logger.warning(f"Failed to get persistent cache stats: {e}")
                stats["persistent_cache_size"] = 0
                stats["model_distribution"] = {}

        return stats

    def clear_memory(self) -> None:
        """清空内存缓存（线程安全）"""
        with self._cache_lock.write_lock():
            self._memory_cache.clear()
        logger.info("Memory cache cleared")

    def shutdown(self) -> None:
        """关闭缓存并清理资源"""
        with self._db_lock:
            for conn in self._db_connections.values():
                try:
                    conn.close()
                except Exception:
                    pass
            self._db_connections.clear()
        logger.info("Thread-safe embedding cache shutdown completed")


# 简单的读写锁实现
class RWLock:
    """简单的读写锁实现"""

    def __init__(self):
        self._read_ready = threading.Condition(threading.RLock())
        self._readers = 0

    @contextmanager
    def read_lock(self):
        """获取读锁"""
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
        """获取写锁"""
        with self._read_ready:
            while self._readers > 0:
                self._read_ready.wait()
            yield


# 不再对 threading 进行猴子补丁，保持局部使用 RWLock()


# 线程安全的全局缓存实例
_thread_safe_cache: Optional[ThreadSafeEmbeddingCache] = None
_cache_creation_lock = threading.Lock()


def get_thread_safe_embedding_cache() -> ThreadSafeEmbeddingCache:
    """获取线程安全的全局嵌入向量缓存实例（单例模式）"""
    global _thread_safe_cache

    if _thread_safe_cache is None:
        with _cache_creation_lock:
            if _thread_safe_cache is None:  # 双重检查锁定
                settings = get_settings()
                cache_size = int(getattr(settings, "embedding_cache_size", 10000))
                enable_persistent = bool(getattr(settings, "embedding_cache_persistent", True))
                _thread_safe_cache = ThreadSafeEmbeddingCache(cache_size, enable_persistent)

    return _thread_safe_cache
