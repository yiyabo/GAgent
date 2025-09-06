"""
LLM Response Cache System

Provides intelligent caching for LLM API responses to reduce costs and improve performance.
Supports both in-memory and persistent storage with TTL and cache size management.
"""

import hashlib
import json
import logging
import sqlite3
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class LRUCache:
    """Thread-safe LRU cache implementation for in-memory caching."""

    def __init__(self, max_size: int = 1000):
        """
        Initialize LRU cache.

        Args:
            max_size: Maximum number of items to store in cache
        """
        self.max_size = max_size
        self.cache: OrderedDict = OrderedDict()
        self.lock = threading.RLock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[Any]:
        """Get item from cache."""
        with self.lock:
            if key in self.cache:
                # Move to end (most recently used)
                self.cache.move_to_end(key)
                self.hits += 1
                return self.cache[key]
            self.misses += 1
            return None

    def set(self, key: str, value: Any):
        """Set item in cache."""
        with self.lock:
            if key in self.cache:
                # Update existing and move to end
                self.cache.move_to_end(key)
            else:
                # Add new item
                if len(self.cache) >= self.max_size:
                    # Remove least recently used
                    self.cache.popitem(last=False)
            self.cache[key] = value

    def clear(self):
        """Clear all items from cache."""
        with self.lock:
            self.cache.clear()
            self.hits = 0
            self.misses = 0

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        with self.lock:
            total = self.hits + self.misses
            hit_rate = self.hits / total if total > 0 else 0
            return {
                "size": len(self.cache),
                "max_size": self.max_size,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": hit_rate,
            }


class LLMCache:
    """
    Comprehensive LLM response caching system with multi-level storage.

    Features:
    - Two-level caching (memory + SQLite)
    - TTL support for cache expiration
    - Prompt normalization for better hit rates
    - Cost tracking and analytics
    """

    def __init__(
        self, db_path: Optional[str] = None, memory_size: int = 500, ttl_hours: int = 24, enable_persistent: bool = True
    ):
        """
        Initialize LLM cache.

        Args:
            db_path: Path to SQLite cache database
            memory_size: Size of in-memory L1 cache
            ttl_hours: Time-to-live for cached responses in hours
            enable_persistent: Whether to enable persistent L2 cache
        """
        self.memory_cache = LRUCache(max_size=memory_size)
        self.ttl_seconds = ttl_hours * 3600
        self.enable_persistent = enable_persistent
        
        # 使用规范的缓存路径
        if db_path is None:
            from ...config.database_config import get_cache_database_path
            self.db_path = get_cache_database_path("llm")
        else:
            self.db_path = db_path

        # Cost tracking
        self.total_saved_tokens = 0
        self.total_saved_calls = 0

        if self.enable_persistent:
            self._init_persistent_cache()

    def _init_persistent_cache(self):
        """Initialize SQLite persistent cache."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_cache (
                cache_key TEXT PRIMARY KEY,
                prompt TEXT NOT NULL,
                response TEXT NOT NULL,
                model TEXT,
                temperature REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                access_count INTEGER DEFAULT 1,
                token_count INTEGER DEFAULT 0,
                cost_saved REAL DEFAULT 0
            )
        """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_accessed_at 
            ON llm_cache(accessed_at)
        """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_model 
            ON llm_cache(model)
        """
        )
        conn.commit()
        conn.close()

        logger.info(f"Initialized LLM cache database at {self.db_path}")

    def _generate_cache_key(self, prompt: str, model: Optional[str] = None, temperature: Optional[float] = None) -> str:
        """
        Generate cache key from prompt and parameters.

        Args:
            prompt: The prompt text
            model: Model name (optional)
            temperature: Temperature setting (optional)

        Returns:
            Cache key hash
        """
        # Normalize prompt (remove extra whitespace, lowercase)
        normalized = " ".join(prompt.lower().split())

        # Include model and temperature in key if provided
        key_parts = [normalized]
        if model:
            key_parts.append(f"model:{model}")
        if temperature is not None:
            key_parts.append(f"temp:{temperature:.2f}")

        key_string = "|".join(key_parts)
        return hashlib.sha256(key_string.encode()).hexdigest()

    def get(self, prompt: str, model: Optional[str] = None, temperature: Optional[float] = None) -> Optional[str]:
        """
        Get cached response for prompt.

        Args:
            prompt: The prompt to look up
            model: Model name
            temperature: Temperature setting

        Returns:
            Cached response or None if not found/expired
        """
        cache_key = self._generate_cache_key(prompt, model, temperature)

        # Check L1 memory cache first
        cached = self.memory_cache.get(cache_key)
        if cached:
            # Check if not expired
            if time.time() - cached["timestamp"] < self.ttl_seconds:
                logger.debug(f"L1 cache hit for key {cache_key[:8]}...")
                self.total_saved_calls += 1
                return cached["response"]

        # Check L2 persistent cache
        if self.enable_persistent:
            cached = self._get_from_persistent(cache_key)
            if cached:
                # Add to memory cache for faster future access
                self.memory_cache.set(cache_key, {"response": cached, "timestamp": time.time()})
                logger.debug(f"L2 cache hit for key {cache_key[:8]}...")
                self.total_saved_calls += 1
                return cached

        logger.debug(f"Cache miss for key {cache_key[:8]}...")
        return None

    def _get_from_persistent(self, cache_key: str) -> Optional[str]:
        """Get response from persistent cache."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute(
                """
                SELECT response, created_at, token_count
                FROM llm_cache
                WHERE cache_key = ?
            """,
                (cache_key,),
            )

            row = cursor.fetchone()
            if row:
                response, created_at, token_count = row
                # Check TTL
                created_time = datetime.fromisoformat(created_at)
                if datetime.now() - created_time < timedelta(seconds=self.ttl_seconds):
                    # Update access stats
                    conn.execute(
                        """
                        UPDATE llm_cache
                        SET accessed_at = CURRENT_TIMESTAMP,
                            access_count = access_count + 1
                        WHERE cache_key = ?
                    """,
                        (cache_key,),
                    )
                    conn.commit()

                    self.total_saved_tokens += token_count or 0
                    conn.close()
                    return response

            conn.close()
        except Exception as e:
            logger.error(f"Error accessing persistent cache: {e}")

        return None

    def set(
        self,
        prompt: str,
        response: str,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        token_count: int = 0,
        cost: float = 0,
    ):
        """
        Cache LLM response.

        Args:
            prompt: The prompt that generated this response
            response: The LLM response to cache
            model: Model name
            temperature: Temperature setting
            token_count: Number of tokens in response
            cost: Cost of the API call
        """
        cache_key = self._generate_cache_key(prompt, model, temperature)

        # Add to L1 memory cache
        self.memory_cache.set(cache_key, {"response": response, "timestamp": time.time()})

        # Add to L2 persistent cache
        if self.enable_persistent:
            self._set_in_persistent(cache_key, prompt, response, model, temperature, token_count, cost)

        logger.debug(f"Cached response for key {cache_key[:8]}...")

    def _set_in_persistent(
        self,
        cache_key: str,
        prompt: str,
        response: str,
        model: Optional[str],
        temperature: Optional[float],
        token_count: int,
        cost: float,
    ):
        """Store response in persistent cache."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                """
                INSERT OR REPLACE INTO llm_cache
                (cache_key, prompt, response, model, temperature, 
                 token_count, cost_saved, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
                (cache_key, prompt, response, model, temperature, token_count, cost),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error storing in persistent cache: {e}")

    def get_or_compute(
        self,
        prompt: str,
        llm_func: Callable,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        **llm_kwargs,
    ) -> Tuple[str, bool]:
        """
        Get cached response or compute using LLM function.

        Args:
            prompt: The prompt to process
            llm_func: Function to call if cache miss
            model: Model name
            temperature: Temperature setting
            **llm_kwargs: Additional arguments for llm_func

        Returns:
            Tuple of (response, was_cached)
        """
        # Try to get from cache
        cached = self.get(prompt, model, temperature)
        if cached:
            return cached, True

        # Compute using LLM
        try:
            response = llm_func(prompt, **llm_kwargs)

            # Extract token count and cost if available
            token_count = llm_kwargs.get("token_count", 0)
            cost = llm_kwargs.get("cost", 0)

            # Cache the response
            self.set(prompt, response, model, temperature, token_count, cost)

            return response, False

        except Exception as e:
            logger.error(f"Error computing LLM response: {e}")
            raise

    def clear_expired(self):
        """Clear expired entries from cache."""
        if not self.enable_persistent:
            return

        try:
            conn = sqlite3.connect(self.db_path)
            cutoff_time = datetime.now() - timedelta(seconds=self.ttl_seconds)

            cursor = conn.execute(
                """
                DELETE FROM llm_cache
                WHERE created_at < ?
            """,
                (cutoff_time.isoformat(),),
            )

            deleted = cursor.rowcount
            conn.commit()
            conn.close()

            if deleted > 0:
                logger.info(f"Cleared {deleted} expired cache entries")

        except Exception as e:
            logger.error(f"Error clearing expired cache: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive cache statistics."""
        stats = {
            "memory_cache": self.memory_cache.get_stats(),
            "total_saved_calls": self.total_saved_calls,
            "total_saved_tokens": self.total_saved_tokens,
        }

        if self.enable_persistent:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.execute(
                    """
                    SELECT 
                        COUNT(*) as total_entries,
                        SUM(access_count) as total_accesses,
                        SUM(token_count) as total_tokens_saved,
                        SUM(cost_saved) as total_cost_saved,
                        AVG(access_count) as avg_access_count
                    FROM llm_cache
                """
                )

                row = cursor.fetchone()
                if row:
                    stats["persistent_cache"] = {
                        "total_entries": row[0],
                        "total_accesses": row[1] or 0,
                        "total_tokens_saved": row[2] or 0,
                        "total_cost_saved": row[3] or 0,
                        "avg_access_count": row[4] or 0,
                    }

                conn.close()

            except Exception as e:
                logger.error(f"Error getting cache stats: {e}")
                stats["persistent_cache"] = {"error": str(e)}

        return stats

    def export_analytics(self, output_path: str = "cache_analytics.json"):
        """Export detailed cache analytics."""
        analytics = {"summary": self.get_stats(), "timestamp": datetime.now().isoformat()}

        if self.enable_persistent:
            try:
                conn = sqlite3.connect(self.db_path)

                # Top accessed prompts
                cursor = conn.execute(
                    """
                    SELECT prompt, model, access_count, cost_saved
                    FROM llm_cache
                    ORDER BY access_count DESC
                    LIMIT 20
                """
                )

                analytics["top_prompts"] = [
                    {
                        "prompt": row[0][:100] + "..." if len(row[0]) > 100 else row[0],
                        "model": row[1],
                        "access_count": row[2],
                        "cost_saved": row[3],
                    }
                    for row in cursor.fetchall()
                ]

                # Model usage distribution
                cursor = conn.execute(
                    """
                    SELECT model, COUNT(*) as count, SUM(token_count) as tokens
                    FROM llm_cache
                    GROUP BY model
                """
                )

                analytics["model_distribution"] = [
                    {"model": row[0], "count": row[1], "tokens": row[2]} for row in cursor.fetchall()
                ]

                conn.close()

            except Exception as e:
                logger.error(f"Error exporting analytics: {e}")
                analytics["error"] = str(e)

        # Save to file
        with open(output_path, "w") as f:
            json.dump(analytics, f, indent=2)

        logger.info(f"Exported cache analytics to {output_path}")
        return analytics


# Global cache instance
_llm_cache: Optional[LLMCache] = None


def get_llm_cache() -> LLMCache:
    """Get global LLM cache instance."""
    global _llm_cache
    if _llm_cache is None:
        _llm_cache = LLMCache()
    return _llm_cache


def initialize_llm_cache(**kwargs) -> LLMCache:
    """Initialize global LLM cache with custom settings."""
    global _llm_cache
    _llm_cache = LLMCache(**kwargs)
    return _llm_cache
