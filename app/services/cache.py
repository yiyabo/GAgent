#!/usr/bin/env python3
"""
Embedding缓存管理模块

提供高效的embedding缓存机制，避免重复计算相同文本的向量，
支持内存缓存和持久化存储两种模式。
"""

import os
import json
import hashlib
import sqlite3
import time
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass
import logging

from .config import get_config

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """缓存条目数据类"""
    text_hash: str
    embedding: List[float]
    model: str
    created_at: float
    access_count: int = 0
    last_accessed: float = 0.0


class EmbeddingCache:
    """Embedding缓存管理器"""
    
    def __init__(self, cache_size: int = 10000, enable_persistent: bool = True):
        self.config = get_config()
        self.cache_size = cache_size
        self.enable_persistent = enable_persistent
        
        # 内存缓存：text_hash -> CacheEntry
        self._memory_cache: Dict[str, CacheEntry] = {}
        
        # 持久化缓存数据库路径
        self.cache_db_path = os.path.join(
            os.path.dirname(os.getenv('DB_PATH', 'tasks.db')), 
            'embedding_cache.db'
        )
        
        if self.enable_persistent:
            self._init_persistent_cache()
        
        logger.info(f"Embedding cache initialized: memory_size={cache_size}, persistent={enable_persistent}")
    
    def _init_persistent_cache(self):
        """初始化持久化缓存数据库"""
        try:
            with sqlite3.connect(self.cache_db_path) as conn:
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS embedding_cache (
                        text_hash TEXT PRIMARY KEY,
                        embedding_json TEXT NOT NULL,
                        model TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        access_count INTEGER DEFAULT 0,
                        last_accessed REAL DEFAULT 0.0
                    )
                ''')
                conn.execute('CREATE INDEX IF NOT EXISTS idx_model ON embedding_cache(model)')
                conn.execute('CREATE INDEX IF NOT EXISTS idx_last_accessed ON embedding_cache(last_accessed)')
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to initialize persistent cache: {e}")
            self.enable_persistent = False
    
    def _compute_text_hash(self, text: str, model: str) -> str:
        """计算文本和模型的哈希值"""
        content = f"{model}:{text}"
        return hashlib.sha256(content.encode('utf-8')).hexdigest()
    
    def get(self, text: str, model: str = None) -> Optional[List[float]]:
        """从缓存获取embedding"""
        if not text.strip():
            return None
        
        model = model or self.config.embedding_model
        text_hash = self._compute_text_hash(text, model)
        current_time = time.time()
        
        # 1. 先查内存缓存
        if text_hash in self._memory_cache:
            entry = self._memory_cache[text_hash]
            entry.access_count += 1
            entry.last_accessed = current_time
            logger.debug(f"Cache hit (memory): {text_hash[:8]}...")
            return entry.embedding.copy()
        
        # 2. 查持久化缓存
        if self.enable_persistent:
            try:
                with sqlite3.connect(self.cache_db_path) as conn:
                    row = conn.execute(
                        'SELECT embedding_json, access_count FROM embedding_cache WHERE text_hash = ? AND model = ?',
                        (text_hash, model)
                    ).fetchone()
                    
                    if row:
                        embedding = json.loads(row[0])
                        access_count = row[1] + 1
                        
                        # 更新访问统计
                        conn.execute(
                            'UPDATE embedding_cache SET access_count = ?, last_accessed = ? WHERE text_hash = ?',
                            (access_count, current_time, text_hash)
                        )
                        conn.commit()
                        
                        # 加载到内存缓存
                        self._add_to_memory_cache(CacheEntry(
                            text_hash=text_hash,
                            embedding=embedding,
                            model=model,
                            created_at=current_time,
                            access_count=access_count,
                            last_accessed=current_time
                        ))
                        
                        logger.debug(f"Cache hit (persistent): {text_hash[:8]}...")
                        return embedding.copy()
            except Exception as e:
                logger.warning(f"Failed to read from persistent cache: {e}")
        
        logger.debug(f"Cache miss: {text_hash[:8]}...")
        return None
    
    def put(self, text: str, embedding: List[float], model: str = None) -> None:
        """将embedding存入缓存"""
        if not text.strip() or not embedding:
            return
        
        model = model or self.config.embedding_model
        text_hash = self._compute_text_hash(text, model)
        current_time = time.time()
        
        entry = CacheEntry(
            text_hash=text_hash,
            embedding=embedding.copy(),
            model=model,
            created_at=current_time,
            access_count=1,
            last_accessed=current_time
        )
        
        # 存入内存缓存
        self._add_to_memory_cache(entry)
        
        # 存入持久化缓存
        if self.enable_persistent:
            try:
                with sqlite3.connect(self.cache_db_path) as conn:
                    conn.execute('''
                        INSERT OR REPLACE INTO embedding_cache 
                        (text_hash, embedding_json, model, created_at, access_count, last_accessed)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (
                        text_hash,
                        json.dumps(embedding),
                        model,
                        current_time,
                        1,
                        current_time
                    ))
                    conn.commit()
            except Exception as e:
                logger.warning(f"Failed to write to persistent cache: {e}")
        
        logger.debug(f"Cache stored: {text_hash[:8]}...")
    
    def _add_to_memory_cache(self, entry: CacheEntry) -> None:
        """添加条目到内存缓存，处理容量限制"""
        # 如果缓存已满，移除最少使用的条目
        if len(self._memory_cache) >= self.cache_size:
            self._evict_lru()
        
        self._memory_cache[entry.text_hash] = entry
    
    def _evict_lru(self) -> None:
        """移除最少使用的缓存条目"""
        if not self._memory_cache:
            return
        
        # 找到最少访问且最久未使用的条目
        lru_key = min(
            self._memory_cache.keys(),
            key=lambda k: (
                self._memory_cache[k].access_count,
                self._memory_cache[k].last_accessed
            )
        )
        
        del self._memory_cache[lru_key]
        logger.debug(f"Evicted from memory cache: {lru_key[:8]}...")
    
    def get_batch(self, texts: List[str], model: str = None) -> Tuple[List[Optional[List[float]]], List[str]]:
        """批量获取embeddings，返回(结果列表, 未命中的文本列表)"""
        model = model or self.config.embedding_model
        results = []
        cache_misses = []
        
        for text in texts:
            embedding = self.get(text, model)
            results.append(embedding)
            if embedding is None:
                cache_misses.append(text)
        
        return results, cache_misses
    
    def put_batch(self, texts: List[str], embeddings: List[List[float]], model: str = None) -> None:
        """批量存储embeddings"""
        if len(texts) != len(embeddings):
            raise ValueError("texts and embeddings must have the same length")
        
        for text, embedding in zip(texts, embeddings):
            self.put(text, embedding, model)
    
    def clear_memory(self) -> None:
        """清空内存缓存"""
        self._memory_cache.clear()
        logger.info("Memory cache cleared")
    
    def clear_persistent(self) -> None:
        """清空持久化缓存"""
        if self.enable_persistent:
            try:
                with sqlite3.connect(self.cache_db_path) as conn:
                    conn.execute('DELETE FROM embedding_cache')
                    conn.commit()
                logger.info("Persistent cache cleared")
            except Exception as e:
                logger.error(f"Failed to clear persistent cache: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        stats = {
            "memory_cache_size": len(self._memory_cache),
            "memory_cache_limit": self.cache_size,
            "persistent_enabled": self.enable_persistent,
        }
        
        if self.enable_persistent:
            try:
                with sqlite3.connect(self.cache_db_path) as conn:
                    row = conn.execute('SELECT COUNT(*) FROM embedding_cache').fetchone()
                    stats["persistent_cache_size"] = row[0] if row else 0
                    
                    # 获取模型分布
                    model_rows = conn.execute(
                        'SELECT model, COUNT(*) FROM embedding_cache GROUP BY model'
                    ).fetchall()
                    stats["model_distribution"] = {row[0]: row[1] for row in model_rows}
            except Exception as e:
                logger.warning(f"Failed to get persistent cache stats: {e}")
                stats["persistent_cache_size"] = 0
                stats["model_distribution"] = {}
        
        return stats
    
    def cleanup_old_entries(self, days: int = 30) -> int:
        """清理旧的缓存条目"""
        if not self.enable_persistent:
            return 0
        
        cutoff_time = time.time() - (days * 24 * 3600)
        
        try:
            with sqlite3.connect(self.cache_db_path) as conn:
                cursor = conn.execute(
                    'DELETE FROM embedding_cache WHERE last_accessed < ?',
                    (cutoff_time,)
                )
                deleted_count = cursor.rowcount
                conn.commit()
                
                logger.info(f"Cleaned up {deleted_count} old cache entries (older than {days} days)")
                return deleted_count
        except Exception as e:
            logger.error(f"Failed to cleanup old cache entries: {e}")
            return 0


# 全局缓存实例
_embedding_cache: Optional[EmbeddingCache] = None


def get_embedding_cache() -> EmbeddingCache:
    """获取全局embedding缓存实例"""
    global _embedding_cache
    if _embedding_cache is None:
        cache_size = int(os.getenv('EMBEDDING_CACHE_SIZE', '10000'))
        enable_persistent = os.getenv('EMBEDDING_CACHE_PERSISTENT', '1') == '1'
        _embedding_cache = EmbeddingCache(cache_size, enable_persistent)
    return _embedding_cache
