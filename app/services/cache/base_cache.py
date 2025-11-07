"""
Base Cache Implementation

Provides a unified caching foundation with thread-safe operations,
TTL management, and persistent storage support.
"""

import hashlib
import json
import logging
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """
    Unified cache entry with metadata.
    
    Attributes:
        key: Cache key
        value: Cached value
        ttl: Time to live in seconds (default: 3600)
        created_at: Creation timestamp
        last_accessed: Last access timestamp
        access_count: Number of times accessed
    """
    key: str
    value: Any
    ttl: int = 3600
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 1

    def is_expired(self, current_time: Optional[float] = None) -> bool:
        """Check if cache entry is expired."""
        current_time = current_time or time.time()
        return (current_time - self.created_at) > self.ttl

    def update_access(self, current_time: Optional[float] = None) -> None:
        """Update access statistics."""
        current_time = current_time or time.time()
        self.last_accessed = current_time
        self.access_count += 1

    def serialize(self) -> str:
        """Serialize cache entry to JSON string."""
        return json.dumps({
            'key': self.key,
            'value': self.value,
            'ttl': self.ttl,
            'created_at': self.created_at,
            'last_accessed': self.last_accessed,
            'access_count': self.access_count,
        })

    @classmethod
    def deserialize(cls, data: str) -> 'CacheEntry':
        """Deserialize JSON string to cache entry."""
        obj = json.loads(data)
        return cls(**obj)


class BaseCache(ABC):
    """
    Abstract base class for all cache implementations.
    
    Provides common caching functionality including:
    - Thread-safe operations
    - TTL management
    - Statistics tracking
    - Persistent storage
    """
    
    def __init__(
        self,
        cache_name: str,
        max_size: int = 1000,
        default_ttl: int = 3600,
        enable_persistent: bool = True,
        cleanup_interval: int = 300
    ):
        """
        Initialize base cache.
        
        Args:
            cache_name: Name of the cache (used for database file)
            max_size: Maximum number of entries in memory cache
            default_ttl: Default TTL in seconds
            enable_persistent: Whether to enable persistent storage
            cleanup_interval: Cleanup interval in seconds
        """
        self.cache_name = cache_name
        self.max_size = max_size
        self.default_ttl = default_ttl
        self.enable_persistent = enable_persistent
        self.cleanup_interval = cleanup_interval
        
        # Memory cache (key -> CacheEntry)
        self._memory_cache: Dict[str, CacheEntry] = {}
        
        # Thread safety
        self._lock = threading.RLock()
        
        # Statistics
        self._stats = {
            'hits': 0,
            'misses': 0,
            'evictions': 0,
            'total_requests': 0,
        }
        
        # Database setup
        if self.enable_persistent:
            self._db_path = self._get_db_path()
            self._init_database()
        
        # Background cleanup
        self._start_cleanup_thread()

    def _get_db_path(self) -> str:
        """Get database file path for this cache."""
        from ...config.database_config import get_cache_database_path
        return get_cache_database_path(self.cache_name)

    def _init_database(self) -> None:
        """Initialize SQLite database for persistent storage."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS cache_entries (
                        key TEXT PRIMARY KEY,
                        entry_data TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        last_accessed REAL NOT NULL,
                        access_count INTEGER NOT NULL,
                        ttl INTEGER NOT NULL
                    )
                ''')
                conn.execute('''
                    CREATE INDEX IF NOT EXISTS idx_last_accessed 
                    ON cache_entries(last_accessed)
                ''')
                conn.execute('''
                    CREATE INDEX IF NOT EXISTS idx_created_at 
                    ON cache_entries(created_at)
                ''')
                conn.commit()
            logger.info(f"Initialized cache database: {self._db_path}")
        except Exception as e:
            logger.error(f"Failed to initialize cache database {self._db_path}: {e}")
            raise

    def _start_cleanup_thread(self) -> None:
        """Start background cleanup thread."""
        def cleanup_worker():
            while True:
                try:
                    time.sleep(self.cleanup_interval)
                    self._cleanup_expired()
                    self._enforce_memory_limit()
                except Exception as e:
                    logger.error(f"Cache cleanup error: {e}")
                    time.sleep(60)  # Wait before retry

        cleanup_thread = threading.Thread(target=cleanup_worker, daemon=True)
        cleanup_thread.start()

    @abstractmethod
    def _generate_key(self, *args, **kwargs) -> str:
        """
        Generate cache key from input parameters.
        
        Must be implemented by subclasses.
        """
        pass

    def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache.
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None if not found/expired
        """
        with self._lock:
            self._stats['total_requests'] += 1
            
            # Check memory cache
            if key in self._memory_cache:
                entry = self._memory_cache[key]
                
                if entry.is_expired():
                    del self._memory_cache[key]
                    if self.enable_persistent:
                        self._delete_from_db(key)
                    self._stats['misses'] += 1
                    return None
                
                entry.update_access()
                self._update_in_db(entry)
                self._stats['hits'] += 1
                return entry.value
            
            # Check persistent storage
            if self.enable_persistent:
                entry = self._load_from_db(key)
                if entry:
                    if not entry.is_expired():
                        self._memory_cache[key] = entry
                        entry.update_access()
                        self._update_in_db(entry)
                        self._stats['hits'] += 1
                        return entry.value
                    else:
                        self._delete_from_db(key)
            
            self._stats['misses'] += 1
            return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """
        Set value in cache.
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: TTL in seconds (uses default if None)
        """
        if ttl is None:
            ttl = self.default_ttl
            
        entry = CacheEntry(key=key, value=value, ttl=ttl)
        
        with self._lock:
            self._memory_cache[key] = entry
            
            if self.enable_persistent:
                self._save_to_db(entry)

    def delete(self, key: str) -> bool:
        """
        Delete entry from cache.
        
        Args:
            key: Cache key
            
        Returns:
            True if entry was deleted, False if not found
        """
        with self._lock:
            deleted = False
            
            if key in self._memory_cache:
                del self._memory_cache[key]
                deleted = True
            
            if self.enable_persistent:
                if self._delete_from_db(key):
                    deleted = True
            
            return deleted

    def clear(self) -> None:
        """Clear all entries from cache."""
        with self._lock:
            self._memory_cache.clear()
            
            if self.enable_persistent:
                try:
                    with sqlite3.connect(self._db_path) as conn:
                        conn.execute('DELETE FROM cache_entries')
                        conn.commit()
                    logger.info(f"Cleared cache database: {self._db_path}")
                except Exception as e:
                    logger.error(f"Failed to clear cache database: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache statistics
        """
        with self._lock:
            hit_rate = 0
            if self._stats['total_requests'] > 0:
                hit_rate = self._stats['hits'] / self._stats['total_requests']

            return {
                'cache_name': self.cache_name,
                'hits': self._stats['hits'],
                'misses': self._stats['misses'],
                'total_requests': self._stats['total_requests'],
                'hit_rate': hit_rate * 100,  # Convert to percentage
                'current_size': len(self._memory_cache),
                'memory_size': len(self._memory_cache),  # Keep for backward compatibility
                'max_size': self.max_size,
                'default_ttl': self.default_ttl,
                'enable_persistent': self.enable_persistent,
            }

    def _save_to_db(self, entry: CacheEntry) -> None:
        """Save entry to database."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute('''
                    INSERT OR REPLACE INTO cache_entries 
                    (key, entry_data, created_at, last_accessed, access_count, ttl)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    entry.key,
                    entry.serialize(),
                    entry.created_at,
                    entry.last_accessed,
                    entry.access_count,
                    entry.ttl
                ))
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to save entry to database: {e}")

    def _load_from_db(self, key: str) -> Optional[CacheEntry]:
        """Load entry from database."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                cursor = conn.execute(
                    'SELECT entry_data FROM cache_entries WHERE key = ?',
                    (key,)
                )
                row = cursor.fetchone()
                if row:
                    return CacheEntry.deserialize(row[0])
        except Exception as e:
            logger.error(f"Failed to load entry from database: {e}")
        return None

    def _update_in_db(self, entry: CacheEntry) -> None:
        """Update entry in database."""
        self._save_to_db(entry)  # Use save to handle both insert and update

    def _delete_from_db(self, key: str) -> bool:
        """Delete entry from database."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                cursor = conn.execute(
                    'DELETE FROM cache_entries WHERE key = ?',
                    (key,)
                )
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Failed to delete entry from database: {e}")
            return False

    def _cleanup_expired(self) -> int:
        """Remove expired entries from cache."""
        current_time = time.time()
        expired_keys = []
        
        with self._lock:
            # Check memory cache
            for key, entry in self._memory_cache.items():
                if entry.is_expired(current_time):
                    expired_keys.append(key)
            
            # Remove expired entries
            for key in expired_keys:
                del self._memory_cache[key]
                if self.enable_persistent:
                    self._delete_from_db(key)
            
            # Also cleanup expired entries from database
            if self.enable_persistent:
                try:
                    with sqlite3.connect(self._db_path) as conn:
                        cursor = conn.execute('''
                            DELETE FROM cache_entries 
                            WHERE created_at + ttl < ?
                        ''', (current_time,))
                        deleted_count = cursor.rowcount + len(expired_keys)
                        conn.commit()
                        if deleted_count > 0:
                            logger.debug(f"Cleaned {deleted_count} expired entries from {self.cache_name}")
                        return deleted_count
                except Exception as e:
                    logger.error(f"Failed to cleanup expired entries: {e}")
            
            return len(expired_keys)

    def _enforce_memory_limit(self) -> int:
        """Enforce memory cache size limit using LRU eviction."""
        if len(self._memory_cache) <= self.max_size:
            return 0
        
        # Sort by last accessed time (LRU)
        sorted_entries = sorted(
            self._memory_cache.items(),
            key=lambda x: x[1].last_accessed
        )
        
        evicted_count = len(self._memory_cache) - self.max_size
        evicted = []
        
        with self._lock:
            for key, _ in sorted_entries[:evicted_count]:
                del self._memory_cache[key]
                if self.enable_persistent:
                    self._delete_from_db(key)
                evicted.append(key)
                self._stats['evictions'] += 1
        
        if evicted_count > 0:
            logger.debug(f"Evicted {evicted_count} entries from {self.cache_name} cache")
        
        return evicted_count

    def get_or_compute(
        self,
        key: str,
        compute_func: callable,
        ttl: Optional[int] = None,
        *args,
        **kwargs
    ) -> Any:
        """
        Get value from cache or compute and cache it.
        
        Args:
            key: Cache key
            compute_func: Function to compute value if not in cache
            ttl: TTL in seconds
            *args, **kwargs: Arguments for compute_func
            
        Returns:
            Cached or computed value
        """
        value = self.get(key)
        if value is not None:
            return value
        
        # Compute value
        try:
            value = compute_func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error computing value for key {key}: {e}")
            raise
        
        # Cache computed value
        self.set(key, value, ttl)
        return value