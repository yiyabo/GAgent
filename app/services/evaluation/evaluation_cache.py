"""
Evaluation Results Cache and Performance Optimization.

Provides caching mechanisms for evaluation results to improve performance
and reduce redundant computations in the evaluation system.
"""

import hashlib
import json
import logging
import sqlite3
import threading
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ...models import EvaluationDimensions, EvaluationResult

logger = logging.getLogger(__name__)


class EvaluationCache:
    """Cache system for evaluation results with performance optimization"""

    def __init__(self, cache_db_path: str = "evaluation_cache.db", max_cache_size: int = 10000):
        self.cache_db_path = cache_db_path
        self.max_cache_size = max_cache_size
        self.memory_cache = {}
        self.cache_stats = {"hits": 0, "misses": 0, "evictions": 0, "total_requests": 0}
        self._lock = threading.RLock()
        self._init_cache_db()

    def _init_cache_db(self):
        """Initialize cache database"""
        try:
            with sqlite3.connect(self.cache_db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS evaluation_cache (
                        cache_key TEXT PRIMARY KEY,
                        content_hash TEXT NOT NULL,
                        task_context_hash TEXT NOT NULL,
                        evaluation_method TEXT NOT NULL,
                        evaluation_result TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        access_count INTEGER DEFAULT 1,
                        cache_metadata TEXT
                    )
                """
                )

                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_content_hash 
                    ON evaluation_cache(content_hash)
                """
                )

                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_method_hash 
                    ON evaluation_cache(evaluation_method, content_hash)
                """
                )

                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_last_accessed 
                    ON evaluation_cache(last_accessed)
                """
                )

                conn.commit()
                logger.debug("Evaluation cache database initialized")

        except Exception as e:
            logger.error(f"Failed to initialize cache database: {e}")

    def _generate_cache_key(
        self,
        content: str,
        task_context: Dict[str, Any],
        evaluation_method: str,
        config_params: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate unique cache key for evaluation request"""

        # Create content hash
        content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()

        # Create context hash (only include relevant fields)
        context_for_hash = {
            "name": task_context.get("name", ""),
            "task_type": task_context.get("task_type", ""),
            "task_id": task_context.get("task_id", ""),
        }
        context_str = json.dumps(context_for_hash, sort_keys=True)
        context_hash = hashlib.md5(context_str.encode("utf-8")).hexdigest()

        # Include config parameters if provided
        config_hash = ""
        if config_params:
            config_str = json.dumps(config_params, sort_keys=True)
            config_hash = hashlib.md5(config_str.encode("utf-8")).hexdigest()

        # Combine all components
        cache_key_data = f"{evaluation_method}:{content_hash}:{context_hash}:{config_hash}"
        cache_key = hashlib.sha256(cache_key_data.encode("utf-8")).hexdigest()

        return cache_key

    def get_cached_evaluation(
        self,
        content: str,
        task_context: Dict[str, Any],
        evaluation_method: str,
        config_params: Optional[Dict[str, Any]] = None,
        max_age_hours: int = 24,
    ) -> Optional[EvaluationResult]:
        """
        Retrieve cached evaluation result

        Args:
            content: Content that was evaluated
            task_context: Task context information
            evaluation_method: Method used for evaluation
            config_params: Configuration parameters used
            max_age_hours: Maximum age of cached result in hours

        Returns:
            Cached EvaluationResult or None if not found/expired
        """

        with self._lock:
            self.cache_stats["total_requests"] += 1

            cache_key = self._generate_cache_key(content, task_context, evaluation_method, config_params)

            # Check memory cache first
            if cache_key in self.memory_cache:
                cached_data = self.memory_cache[cache_key]
                if self._is_cache_valid(cached_data, max_age_hours):
                    self.cache_stats["hits"] += 1
                    logger.debug(f"Cache hit (memory): {cache_key[:16]}...")
                    return self._deserialize_evaluation_result(cached_data["result"])
                else:
                    # Remove expired entry
                    del self.memory_cache[cache_key]

            # Check persistent cache
            try:
                with sqlite3.connect(self.cache_db_path) as conn:
                    cursor = conn.execute(
                        """
                        SELECT evaluation_result, created_at, cache_metadata
                        FROM evaluation_cache 
                        WHERE cache_key = ?
                    """,
                        (cache_key,),
                    )

                    row = cursor.fetchone()
                    if row:
                        result_json, created_at, metadata_json = row

                        # Check if cache entry is still valid
                        created_time = datetime.fromisoformat(created_at)
                        if datetime.now() - created_time <= timedelta(hours=max_age_hours):
                            # Update access statistics
                            conn.execute(
                                """
                                UPDATE evaluation_cache 
                                SET last_accessed = CURRENT_TIMESTAMP, 
                                    access_count = access_count + 1
                                WHERE cache_key = ?
                            """,
                                (cache_key,),
                            )
                            conn.commit()

                            # Add to memory cache for faster future access
                            cached_data = {
                                "result": result_json,
                                "created_at": created_at,
                                "metadata": json.loads(metadata_json) if metadata_json else {},
                            }
                            self._add_to_memory_cache(cache_key, cached_data)

                            self.cache_stats["hits"] += 1
                            logger.debug(f"Cache hit (persistent): {cache_key[:16]}...")
                            return self._deserialize_evaluation_result(result_json)
                        else:
                            # Remove expired entry
                            conn.execute("DELETE FROM evaluation_cache WHERE cache_key = ?", (cache_key,))
                            conn.commit()

            except Exception as e:
                logger.error(f"Error retrieving from cache: {e}")

            self.cache_stats["misses"] += 1
            logger.debug(f"Cache miss: {cache_key[:16]}...")
            return None

    def cache_evaluation_result(
        self,
        content: str,
        task_context: Dict[str, Any],
        evaluation_method: str,
        evaluation_result: EvaluationResult,
        config_params: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Cache evaluation result

        Args:
            content: Content that was evaluated
            task_context: Task context information
            evaluation_method: Method used for evaluation
            evaluation_result: Result to cache
            config_params: Configuration parameters used
            metadata: Additional metadata to store

        Returns:
            True if successfully cached, False otherwise
        """

        try:
            with self._lock:
                cache_key = self._generate_cache_key(content, task_context, evaluation_method, config_params)

                # Serialize evaluation result
                result_json = self._serialize_evaluation_result(evaluation_result)

                # Generate hashes for indexing
                content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
                context_str = json.dumps(
                    {
                        "name": task_context.get("name", ""),
                        "task_type": task_context.get("task_type", ""),
                        "task_id": task_context.get("task_id", ""),
                    },
                    sort_keys=True,
                )
                context_hash = hashlib.md5(context_str.encode("utf-8")).hexdigest()

                # Prepare metadata
                cache_metadata = {
                    "evaluation_score": evaluation_result.overall_score,
                    "needs_revision": evaluation_result.needs_revision,
                    "iteration": evaluation_result.iteration,
                    "content_length": len(content),
                    **(metadata or {}),
                }
                metadata_json = json.dumps(cache_metadata)

                # Store in persistent cache
                with sqlite3.connect(self.cache_db_path) as conn:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO evaluation_cache 
                        (cache_key, content_hash, task_context_hash, evaluation_method, 
                         evaluation_result, cache_metadata)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """,
                        (cache_key, content_hash, context_hash, evaluation_method, result_json, metadata_json),
                    )
                    conn.commit()

                # Add to memory cache
                cached_data = {
                    "result": result_json,
                    "created_at": datetime.now().isoformat(),
                    "metadata": cache_metadata,
                }
                self._add_to_memory_cache(cache_key, cached_data)

                logger.debug(f"Cached evaluation result: {cache_key[:16]}...")

                # Cleanup old entries if cache is getting too large
                self._cleanup_cache_if_needed()

                return True

        except Exception as e:
            logger.error(f"Error caching evaluation result: {e}")
            return False

    def _add_to_memory_cache(self, cache_key: str, cached_data: Dict[str, Any]):
        """Add entry to memory cache with size management"""

        # Remove oldest entries if cache is full
        if len(self.memory_cache) >= self.max_cache_size:
            # Remove 10% of oldest entries
            entries_to_remove = max(1, self.max_cache_size // 10)
            oldest_keys = sorted(self.memory_cache.keys(), key=lambda k: self.memory_cache[k].get("created_at", ""))[
                :entries_to_remove
            ]

            for key in oldest_keys:
                del self.memory_cache[key]
                self.cache_stats["evictions"] += 1

        self.memory_cache[cache_key] = cached_data

    def _is_cache_valid(self, cached_data: Dict[str, Any], max_age_hours: int) -> bool:
        """Check if cached data is still valid"""

        try:
            created_at = cached_data.get("created_at")
            if not created_at:
                return False

            created_time = datetime.fromisoformat(created_at)
            return datetime.now() - created_time <= timedelta(hours=max_age_hours)

        except Exception:
            return False

    def _serialize_evaluation_result(self, result: EvaluationResult) -> str:
        """Serialize EvaluationResult to JSON string"""

        result_dict = {
            "overall_score": result.overall_score,
            "dimensions": asdict(result.dimensions),
            "suggestions": result.suggestions,
            "needs_revision": result.needs_revision,
            "iteration": result.iteration,
            "timestamp": result.timestamp.isoformat() if result.timestamp else None,
            "metadata": result.metadata,
        }

        return json.dumps(result_dict)

    def _deserialize_evaluation_result(self, result_json: str) -> EvaluationResult:
        """Deserialize JSON string to EvaluationResult"""

        result_dict = json.loads(result_json)

        # Reconstruct EvaluationDimensions
        dimensions_dict = result_dict.get("dimensions", {})
        dimensions = EvaluationDimensions(**dimensions_dict)

        # Reconstruct EvaluationResult
        timestamp = None
        if result_dict.get("timestamp"):
            timestamp = datetime.fromisoformat(result_dict["timestamp"])

        return EvaluationResult(
            overall_score=result_dict.get("overall_score", 0.0),
            dimensions=dimensions,
            suggestions=result_dict.get("suggestions", []),
            needs_revision=result_dict.get("needs_revision", True),
            iteration=result_dict.get("iteration", 0),
            timestamp=timestamp,
            metadata=result_dict.get("metadata", {}),
        )

    def _cleanup_cache_if_needed(self):
        """Clean up old cache entries if needed"""

        try:
            with sqlite3.connect(self.cache_db_path) as conn:
                # Count total entries
                cursor = conn.execute("SELECT COUNT(*) FROM evaluation_cache")
                total_entries = cursor.fetchone()[0]

                # If cache is getting large, remove oldest entries
                if total_entries > self.max_cache_size * 2:
                    entries_to_remove = total_entries - self.max_cache_size

                    conn.execute(
                        """
                        DELETE FROM evaluation_cache 
                        WHERE cache_key IN (
                            SELECT cache_key FROM evaluation_cache 
                            ORDER BY last_accessed ASC 
                            LIMIT ?
                        )
                    """,
                        (entries_to_remove,),
                    )

                    conn.commit()
                    logger.info(f"Cleaned up {entries_to_remove} old cache entries")

        except Exception as e:
            logger.error(f"Error during cache cleanup: {e}")

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache performance statistics"""

        with self._lock:
            hit_rate = 0.0
            if self.cache_stats["total_requests"] > 0:
                hit_rate = self.cache_stats["hits"] / self.cache_stats["total_requests"]

            # Get persistent cache stats
            persistent_stats = {}
            try:
                with sqlite3.connect(self.cache_db_path) as conn:
                    cursor = conn.execute("SELECT COUNT(*) FROM evaluation_cache")
                    persistent_stats["total_entries"] = cursor.fetchone()[0]

                    cursor = conn.execute(
                        """
                        SELECT evaluation_method, COUNT(*) 
                        FROM evaluation_cache 
                        GROUP BY evaluation_method
                    """
                    )
                    persistent_stats["entries_by_method"] = dict(cursor.fetchall())

            except Exception as e:
                logger.error(f"Error getting persistent cache stats: {e}")
                persistent_stats = {"error": str(e)}

            return {
                "memory_cache_size": len(self.memory_cache),
                "max_cache_size": self.max_cache_size,
                "hit_rate": hit_rate,
                "cache_stats": self.cache_stats.copy(),
                "persistent_cache": persistent_stats,
            }

    def clear_cache(self, evaluation_method: Optional[str] = None) -> int:
        """
        Clear cache entries

        Args:
            evaluation_method: If specified, only clear entries for this method

        Returns:
            Number of entries cleared
        """

        cleared_count = 0

        with self._lock:
            # Clear memory cache
            if evaluation_method:
                # This is complex for memory cache, so we'll clear all for simplicity
                memory_cleared = len(self.memory_cache)
                self.memory_cache.clear()
                cleared_count += memory_cleared
            else:
                memory_cleared = len(self.memory_cache)
                self.memory_cache.clear()
                cleared_count += memory_cleared

            # Clear persistent cache
            try:
                with sqlite3.connect(self.cache_db_path) as conn:
                    if evaluation_method:
                        cursor = conn.execute(
                            "SELECT COUNT(*) FROM evaluation_cache WHERE evaluation_method = ?", (evaluation_method,)
                        )
                        persistent_cleared = cursor.fetchone()[0]

                        conn.execute("DELETE FROM evaluation_cache WHERE evaluation_method = ?", (evaluation_method,))
                    else:
                        cursor = conn.execute("SELECT COUNT(*) FROM evaluation_cache")
                        persistent_cleared = cursor.fetchone()[0]

                        conn.execute("DELETE FROM evaluation_cache")

                    conn.commit()
                    cleared_count += persistent_cleared

            except Exception as e:
                logger.error(f"Error clearing persistent cache: {e}")

        logger.info(f"Cleared {cleared_count} cache entries")
        return cleared_count

    def optimize_cache(self) -> Dict[str, Any]:
        """Optimize cache performance"""

        optimization_results = {"actions_taken": [], "entries_removed": 0, "performance_improvement": 0.0}

        try:
            with sqlite3.connect(self.cache_db_path) as conn:
                # Remove entries older than 7 days
                cursor = conn.execute(
                    """
                    SELECT COUNT(*) FROM evaluation_cache 
                    WHERE created_at < datetime('now', '-7 days')
                """
                )
                old_entries = cursor.fetchone()[0]

                if old_entries > 0:
                    conn.execute(
                        """
                        DELETE FROM evaluation_cache 
                        WHERE created_at < datetime('now', '-7 days')
                    """
                    )
                    optimization_results["entries_removed"] += old_entries
                    optimization_results["actions_taken"].append(f"Removed {old_entries} entries older than 7 days")

                # Remove entries with very low access count (accessed only once and older than 1 day)
                cursor = conn.execute(
                    """
                    SELECT COUNT(*) FROM evaluation_cache 
                    WHERE access_count = 1 AND created_at < datetime('now', '-1 day')
                """
                )
                low_access_entries = cursor.fetchone()[0]

                if low_access_entries > 0:
                    conn.execute(
                        """
                        DELETE FROM evaluation_cache 
                        WHERE access_count = 1 AND created_at < datetime('now', '-1 day')
                    """
                    )
                    optimization_results["entries_removed"] += low_access_entries
                    optimization_results["actions_taken"].append(f"Removed {low_access_entries} low-access entries")

                # Vacuum database to reclaim space
                conn.execute("VACUUM")
                optimization_results["actions_taken"].append("Vacuumed database")

                conn.commit()

        except Exception as e:
            logger.error(f"Error during cache optimization: {e}")
            optimization_results["error"] = str(e)

        return optimization_results


# Global cache instance
_evaluation_cache = None
_cache_lock = threading.Lock()


def get_evaluation_cache() -> EvaluationCache:
    """Get global evaluation cache instance"""
    global _evaluation_cache

    if _evaluation_cache is None:
        with _cache_lock:
            if _evaluation_cache is None:
                _evaluation_cache = EvaluationCache()

    return _evaluation_cache


def clear_evaluation_cache(evaluation_method: Optional[str] = None) -> int:
    """Clear evaluation cache"""
    cache = get_evaluation_cache()
    return cache.clear_cache(evaluation_method)


def get_cache_stats() -> Dict[str, Any]:
    """Get cache performance statistics"""
    cache = get_evaluation_cache()
    return cache.get_cache_stats()
