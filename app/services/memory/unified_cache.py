"""
Unified Cache Management System.

Provides a centralized caching solution with multi-level storage,
intelligent eviction policies, and comprehensive monitoring.
"""

import hashlib
import json
import logging
import sqlite3
import threading
import time
from collections import OrderedDict, defaultdict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class CacheLevel(Enum):
    """Cache level enumeration."""

    L1_MEMORY = "memory"  # Hot data in memory
    L2_MEMORY = "memory_cold"  # Cold data in memory
    L3_DISK = "disk"  # Persistent disk storage


class CacheEntry:
    """Represents a single cache entry with metadata."""

    def __init__(self, key: str, value: Any, ttl: int = 3600):
        self.key = key
        self.value = value
        self.created_at = time.time()
        self.accessed_at = time.time()
        self.access_count = 1
        self.ttl = ttl
        self.size = self._estimate_size(value)

    def _estimate_size(self, obj: Any) -> int:
        """Estimate object size in bytes."""
        try:
            if isinstance(obj, str):
                return len(obj.encode("utf-8"))
            elif isinstance(obj, (dict, list)):
                return len(json.dumps(obj).encode("utf-8"))
            else:
                return len(str(obj).encode("utf-8"))
        except:
            return 100  # Default size

    def is_expired(self) -> bool:
        """Check if entry has expired."""
        return time.time() - self.created_at > self.ttl

    def touch(self):
        """Update access time and count."""
        self.accessed_at = time.time()
        self.access_count += 1

    def get_heat_score(self) -> float:
        """Calculate heat score for cache eviction."""
        age = time.time() - self.created_at
        recency = time.time() - self.accessed_at

        # Higher score = hotter data
        # Consider both frequency and recency
        frequency_score = min(self.access_count / 10, 1.0)
        recency_score = max(0, 1 - (recency / 3600))  # Decay over 1 hour

        return frequency_score * 0.6 + recency_score * 0.4


class UnifiedCache:
    """
    Unified multi-level cache system with intelligent management.

    Features:
    - Three-level cache hierarchy (L1 hot, L2 cold, L3 persistent)
    - Adaptive cache promotion/demotion
    - Multiple eviction strategies
    - Comprehensive monitoring and analytics
    """

    def __init__(
        self,
        l1_size: int = 100,
        l2_size: int = 500,
        db_path: str = "data/databases/cache/unified_cache.db",
        default_ttl: int = 3600,
        enable_disk: bool = True,
    ):
        """
        Initialize unified cache.

        Args:
            l1_size: Size of L1 hot cache
            l2_size: Size of L2 cold cache
            db_path: Path to persistent cache database
            default_ttl: Default TTL in seconds
            enable_disk: Whether to enable L3 disk cache
        """
        self.l1_cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self.l2_cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self.l1_size = l1_size
        self.l2_size = l2_size
        self.default_ttl = default_ttl
        self.enable_disk = enable_disk
        self.db_path = db_path

        # Thread safety
        self.lock = threading.RLock()

        # Statistics
        self.stats = defaultdict(int)
        self.cache_namespaces: Dict[str, set] = defaultdict(set)

        if self.enable_disk:
            self._init_disk_cache()

    def _init_disk_cache(self):
        """Initialize SQLite disk cache."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache_entries (
                cache_key TEXT PRIMARY KEY,
                namespace TEXT,
                value TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                access_count INTEGER DEFAULT 1,
                ttl INTEGER DEFAULT 3600,
                size INTEGER DEFAULT 0,
                heat_score REAL DEFAULT 0.5
            )
        """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_namespace 
            ON cache_entries(namespace)
        """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_heat_score 
            ON cache_entries(heat_score DESC)
        """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_accessed_at 
            ON cache_entries(accessed_at)
        """
        )
        conn.commit()
        conn.close()

        logger.info(f"Initialized unified cache database at {self.db_path}")

    def _generate_key(self, namespace: str, key: str) -> str:
        """Generate namespaced cache key."""
        combined = f"{namespace}:{key}"
        return hashlib.sha256(combined.encode()).hexdigest()

    def get(self, key: str, namespace: str = "default") -> Optional[Any]:
        """
        Get value from cache.

        Args:
            key: Cache key
            namespace: Cache namespace

        Returns:
            Cached value or None if not found/expired
        """
        cache_key = self._generate_key(namespace, key)

        with self.lock:
            # Check L1 (hot cache)
            if cache_key in self.l1_cache:
                entry = self.l1_cache[cache_key]
                if not entry.is_expired():
                    entry.touch()
                    self.l1_cache.move_to_end(cache_key)
                    self.stats["l1_hits"] += 1
                    return entry.value
                else:
                    del self.l1_cache[cache_key]

            # Check L2 (cold cache)
            if cache_key in self.l2_cache:
                entry = self.l2_cache[cache_key]
                if not entry.is_expired():
                    entry.touch()
                    # Promote to L1 if hot enough
                    if entry.get_heat_score() > 0.7:
                        self._promote_to_l1(cache_key, entry)
                    self.stats["l2_hits"] += 1
                    return entry.value
                else:
                    del self.l2_cache[cache_key]

            # Check L3 (disk cache)
            if self.enable_disk:
                value = self._get_from_disk(cache_key)
                if value is not None:
                    # Create entry and add to L2
                    entry = CacheEntry(cache_key, value, self.default_ttl)
                    self._add_to_l2(cache_key, entry)
                    self.stats["l3_hits"] += 1
                    return value

            self.stats["cache_misses"] += 1
            return None

    def _get_from_disk(self, cache_key: str) -> Optional[Any]:
        """Get value from disk cache."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute(
                """
                SELECT value, created_at, ttl, access_count
                FROM cache_entries
                WHERE cache_key = ?
            """,
                (cache_key,),
            )

            row = cursor.fetchone()
            if row:
                value_json, created_at, ttl, access_count = row
                # Check TTL
                created_time = datetime.fromisoformat(created_at)
                if datetime.now() - created_time < timedelta(seconds=ttl):
                    # Update access stats
                    conn.execute(
                        """
                        UPDATE cache_entries
                        SET accessed_at = CURRENT_TIMESTAMP,
                            access_count = access_count + 1,
                            heat_score = ?
                        WHERE cache_key = ?
                    """,
                        (min(1.0, access_count / 10), cache_key),
                    )
                    conn.commit()
                    conn.close()

                    return json.loads(value_json)

            conn.close()
        except Exception as e:
            logger.error(f"Error reading from disk cache: {e}")

        return None

    def set(self, key: str, value: Any, namespace: str = "default", ttl: Optional[int] = None):
        """
        Set value in cache.

        Args:
            key: Cache key
            value: Value to cache
            namespace: Cache namespace
            ttl: Time-to-live in seconds
        """
        cache_key = self._generate_key(namespace, key)
        ttl = ttl or self.default_ttl

        with self.lock:
            entry = CacheEntry(cache_key, value, ttl)

            # Add to namespace tracking
            self.cache_namespaces[namespace].add(cache_key)

            # Determine initial cache level based on expected usage
            # New entries start in L2 unless explicitly hot
            self._add_to_l2(cache_key, entry)

            # Also persist to disk if enabled
            if self.enable_disk:
                self._save_to_disk(cache_key, namespace, value, ttl)

            self.stats["cache_sets"] += 1

    def _add_to_l1(self, key: str, entry: CacheEntry):
        """Add entry to L1 cache with eviction if needed."""
        if len(self.l1_cache) >= self.l1_size:
            # Evict coldest entry from L1 to L2
            coldest_key = min(self.l1_cache.keys(), key=lambda k: self.l1_cache[k].get_heat_score())
            demoted_entry = self.l1_cache.pop(coldest_key)
            self._add_to_l2(coldest_key, demoted_entry)
            self.stats["l1_evictions"] += 1

        self.l1_cache[key] = entry
        self.l1_cache.move_to_end(key)

    def _add_to_l2(self, key: str, entry: CacheEntry):
        """Add entry to L2 cache with eviction if needed."""
        if len(self.l2_cache) >= self.l2_size:
            # Evict LRU entry from L2
            self.l2_cache.popitem(last=False)
            self.stats["l2_evictions"] += 1

        self.l2_cache[key] = entry
        self.l2_cache.move_to_end(key)

    def _promote_to_l1(self, key: str, entry: CacheEntry):
        """Promote entry from L2 to L1."""
        if key in self.l2_cache:
            del self.l2_cache[key]
        self._add_to_l1(key, entry)
        self.stats["promotions"] += 1

    def _save_to_disk(self, cache_key: str, namespace: str, value: Any, ttl: int):
        """Save entry to disk cache."""
        try:
            conn = sqlite3.connect(self.db_path)
            value_json = json.dumps(value)
            size = len(value_json.encode("utf-8"))

            conn.execute(
                """
                INSERT OR REPLACE INTO cache_entries
                (cache_key, namespace, value, ttl, size, created_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
                (cache_key, namespace, value_json, ttl, size),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error saving to disk cache: {e}")

    def get_or_compute(
        self, key: str, compute_func: Callable, namespace: str = "default", ttl: Optional[int] = None, **kwargs
    ) -> Any:
        """
        Get from cache or compute if missing.

        Args:
            key: Cache key
            compute_func: Function to compute value if cache miss
            namespace: Cache namespace
            ttl: Time-to-live
            **kwargs: Arguments for compute_func

        Returns:
            Cached or computed value
        """
        # Try to get from cache
        value = self.get(key, namespace)
        if value is not None:
            return value

        # Compute value
        value = compute_func(**kwargs)

        # Cache the result
        self.set(key, value, namespace, ttl)

        return value

    def invalidate(self, key: str, namespace: str = "default"):
        """
        Invalidate cache entry.

        Args:
            key: Cache key
            namespace: Cache namespace
        """
        cache_key = self._generate_key(namespace, key)

        with self.lock:
            # Remove from all levels
            if cache_key in self.l1_cache:
                del self.l1_cache[cache_key]
            if cache_key in self.l2_cache:
                del self.l2_cache[cache_key]

            # Remove from namespace tracking
            if cache_key in self.cache_namespaces[namespace]:
                self.cache_namespaces[namespace].remove(cache_key)

            # Remove from disk
            if self.enable_disk:
                try:
                    conn = sqlite3.connect(self.db_path)
                    conn.execute("DELETE FROM cache_entries WHERE cache_key = ?", (cache_key,))
                    conn.commit()
                    conn.close()
                except Exception as e:
                    logger.error(f"Error invalidating disk cache: {e}")

            self.stats["invalidations"] += 1

    def invalidate_namespace(self, namespace: str):
        """Invalidate all entries in a namespace."""
        with self.lock:
            keys_to_remove = list(self.cache_namespaces[namespace])
            for cache_key in keys_to_remove:
                # Remove from memory caches
                if cache_key in self.l1_cache:
                    del self.l1_cache[cache_key]
                if cache_key in self.l2_cache:
                    del self.l2_cache[cache_key]

            # Clear namespace tracking
            self.cache_namespaces[namespace].clear()

            # Remove from disk
            if self.enable_disk:
                try:
                    conn = sqlite3.connect(self.db_path)
                    conn.execute("DELETE FROM cache_entries WHERE namespace = ?", (namespace,))
                    conn.commit()
                    conn.close()
                except Exception as e:
                    logger.error(f"Error invalidating namespace from disk: {e}")

            self.stats["namespace_invalidations"] += 1

    def clear_expired(self):
        """Clear all expired entries from cache."""
        with self.lock:
            # Clear from L1
            expired_l1 = [k for k, v in self.l1_cache.items() if v.is_expired()]
            for key in expired_l1:
                del self.l1_cache[key]

            # Clear from L2
            expired_l2 = [k for k, v in self.l2_cache.items() if v.is_expired()]
            for key in expired_l2:
                del self.l2_cache[key]

            total_expired = len(expired_l1) + len(expired_l2)

            # Clear from disk
            if self.enable_disk:
                try:
                    conn = sqlite3.connect(self.db_path)
                    cursor = conn.execute(
                        """
                        DELETE FROM cache_entries
                        WHERE datetime(created_at, '+' || ttl || ' seconds') < datetime('now')
                    """
                    )
                    disk_expired = cursor.rowcount
                    conn.commit()
                    conn.close()
                    total_expired += disk_expired
                except Exception as e:
                    logger.error(f"Error clearing expired from disk: {e}")

            if total_expired > 0:
                logger.info(f"Cleared {total_expired} expired cache entries")
                self.stats["expired_cleared"] += total_expired

    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive cache statistics."""
        with self.lock:
            l1_heat = (
                sum(e.get_heat_score() for e in self.l1_cache.values()) / len(self.l1_cache) if self.l1_cache else 0
            )
            l2_heat = (
                sum(e.get_heat_score() for e in self.l2_cache.values()) / len(self.l2_cache) if self.l2_cache else 0
            )

            stats = {
                "l1_cache": {
                    "size": len(self.l1_cache),
                    "max_size": self.l1_size,
                    "avg_heat_score": l1_heat,
                    "hits": self.stats["l1_hits"],
                },
                "l2_cache": {
                    "size": len(self.l2_cache),
                    "max_size": self.l2_size,
                    "avg_heat_score": l2_heat,
                    "hits": self.stats["l2_hits"],
                },
                "l3_cache": {"enabled": self.enable_disk, "hits": self.stats["l3_hits"]},
                "overall": {
                    "total_hits": self.stats["l1_hits"] + self.stats["l2_hits"] + self.stats["l3_hits"],
                    "total_misses": self.stats["cache_misses"],
                    "hit_rate": self._calculate_hit_rate(),
                    "sets": self.stats["cache_sets"],
                    "invalidations": self.stats["invalidations"],
                    "promotions": self.stats["promotions"],
                    "evictions": self.stats["l1_evictions"] + self.stats["l2_evictions"],
                },
                "namespaces": {ns: len(keys) for ns, keys in self.cache_namespaces.items()},
            }

            # Add disk cache stats if enabled
            if self.enable_disk:
                try:
                    conn = sqlite3.connect(self.db_path)
                    cursor = conn.execute(
                        """
                        SELECT 
                            COUNT(*) as total_entries,
                            SUM(size) as total_size,
                            AVG(access_count) as avg_access_count,
                            AVG(heat_score) as avg_heat_score
                        FROM cache_entries
                    """
                    )
                    row = cursor.fetchone()
                    if row:
                        stats["l3_cache"].update(
                            {
                                "total_entries": row[0],
                                "total_size_bytes": row[1] or 0,
                                "avg_access_count": row[2] or 0,
                                "avg_heat_score": row[3] or 0,
                            }
                        )
                    conn.close()
                except Exception as e:
                    logger.error(f"Error getting disk cache stats: {e}")

            return stats

    def _calculate_hit_rate(self) -> float:
        """Calculate overall cache hit rate."""
        total_hits = self.stats["l1_hits"] + self.stats["l2_hits"] + self.stats["l3_hits"]
        total_requests = total_hits + self.stats["cache_misses"]
        return total_hits / total_requests if total_requests > 0 else 0.0


# Global cache instance
_unified_cache: Optional[UnifiedCache] = None


def get_unified_cache() -> UnifiedCache:
    """Get global unified cache instance."""
    global _unified_cache
    if _unified_cache is None:
        _unified_cache = UnifiedCache()
    return _unified_cache


def initialize_unified_cache(**kwargs) -> UnifiedCache:
    """Initialize global unified cache with custom settings."""
    global _unified_cache
    _unified_cache = UnifiedCache(**kwargs)
    return _unified_cache
