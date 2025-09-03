"""
Tool Call Caching System

This module provides caching capabilities for tool calls to improve
performance and reduce redundant operations.
"""

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """Cache entry with metadata"""

    key: str
    value: Any
    timestamp: float
    ttl: Optional[int] = None  # Time to live in seconds
    access_count: int = 0
    last_accessed: float = 0.0
    metadata: Optional[Dict[str, Any]] = None


class ToolCache:
    """Cache for tool call results"""

    def __init__(self, max_size: int = 1000, default_ttl: int = 3600):
        self.cache: Dict[str, CacheEntry] = {}
        self.max_size = max_size
        self.default_ttl = default_ttl
        self._lock = asyncio.Lock()

    def _generate_cache_key(self, tool_name: str, parameters: Dict[str, Any]) -> str:
        """Generate cache key from tool name and parameters with collision resistance"""
        # Create a more robust deterministic key
        key_data = {
            "tool": tool_name,
            "params": self._normalize_parameters(parameters),
            "version": "v2",  # Version for cache key format
        }

        # Sort parameters for consistency and handle nested structures
        sorted_params = json.dumps(key_data, sort_keys=True, default=str, ensure_ascii=False)

        # Use SHA-256 with tool name prefix to avoid collisions
        hash_input = f"{tool_name}:{sorted_params}".encode("utf-8")
        hash_hex = hashlib.sha256(hash_input).hexdigest()

        # Add tool name prefix for easier debugging and collision avoidance
        return f"{tool_name}_{hash_hex[:16]}"

    def _normalize_parameters(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize parameters to ensure consistent cache keys"""
        if not isinstance(params, dict):
            return {"value": params}

        normalized = {}
        for key, value in params.items():
            if isinstance(value, dict):
                normalized[key] = self._normalize_parameters(value)
            elif isinstance(value, list):
                # Sort lists if they contain comparable items
                try:
                    normalized[key] = (
                        sorted(value) if value and all(isinstance(x, (str, int, float)) for x in value) else value
                    )
                except TypeError:
                    normalized[key] = value
            else:
                normalized[key] = value

        return normalized

    async def get(self, tool_name: str, parameters: Dict[str, Any]) -> Optional[Any]:
        """Get cached result for tool call"""
        async with self._lock:
            cache_key = self._generate_cache_key(tool_name, parameters)

            if cache_key not in self.cache:
                return None

            entry = self.cache[cache_key]

            # Check if entry has expired
            if self._is_expired(entry):
                del self.cache[cache_key]
                return None

            # Update access statistics
            entry.access_count += 1
            entry.last_accessed = time.time()

            logger.debug(f"Cache hit for tool {tool_name}")
            return entry.value

    async def set(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        value: Any,
        ttl: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Cache result for tool call"""
        async with self._lock:
            cache_key = self._generate_cache_key(tool_name, parameters)

            entry = CacheEntry(
                key=cache_key,
                value=value,
                timestamp=time.time(),
                ttl=ttl or self.default_ttl,
                access_count=1,
                last_accessed=time.time(),
                metadata=metadata,
            )

            # Check if we need to evict entries
            if len(self.cache) >= self.max_size:
                await self._evict_entries()

            self.cache[cache_key] = entry
            logger.debug(f"Cached result for tool {tool_name}")

    async def invalidate(self, tool_name: Optional[str] = None, parameters: Optional[Dict[str, Any]] = None) -> int:
        """Invalidate cache entries"""
        async with self._lock:
            if tool_name and parameters:
                # Invalidate specific entry
                cache_key = self._generate_cache_key(tool_name, parameters)
                if cache_key in self.cache:
                    del self.cache[cache_key]
                    return 1
                return 0

            elif tool_name:
                # Invalidate all entries for a tool (improved with prefix matching)
                keys_to_delete = []
                tool_prefix = f"{tool_name}_"

                for key, entry in self.cache.items():
                    # Use prefix matching for more accurate tool-specific invalidation
                    if key.startswith(tool_prefix):
                        keys_to_delete.append(key)

                for key in keys_to_delete:
                    del self.cache[key]

                return len(keys_to_delete)

            else:
                # Invalidate all entries
                count = len(self.cache)
                self.cache.clear()
                return count

    async def _evict_entries(self) -> None:
        """Evict entries using LRU strategy"""
        if not self.cache:
            return

        # Sort by last accessed time (oldest first)
        sorted_entries = sorted(self.cache.items(), key=lambda x: x[1].last_accessed)

        # Remove oldest entries until we're under the limit
        entries_to_remove = len(self.cache) - self.max_size + 1
        for i in range(entries_to_remove):
            if i < len(sorted_entries):
                del self.cache[sorted_entries[i][0]]

    def _is_expired(self, entry: CacheEntry) -> bool:
        """Check if cache entry has expired"""
        if entry.ttl is None:
            return False

        return (time.time() - entry.timestamp) > entry.ttl

    async def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        async with self._lock:
            total_entries = len(self.cache)
            total_accesses = sum(entry.access_count for entry in self.cache.values())
            expired_entries = sum(1 for entry in self.cache.values() if self._is_expired(entry))

            if total_entries > 0:
                avg_accesses = total_accesses / total_entries
                hit_rate = sum(1 for entry in self.cache.values() if entry.access_count > 1) / total_entries
            else:
                avg_accesses = 0
                hit_rate = 0

            return {
                "total_entries": total_entries,
                "total_accesses": total_accesses,
                "expired_entries": expired_entries,
                "average_accesses_per_entry": avg_accesses,
                "hit_rate": hit_rate,
                "max_size": self.max_size,
                "default_ttl": self.default_ttl,
            }

    async def cleanup_expired(self) -> int:
        """Clean up expired entries"""
        async with self._lock:
            expired_keys = []
            for key, entry in self.cache.items():
                if self._is_expired(entry):
                    expired_keys.append(key)

            for key in expired_keys:
                del self.cache[key]

            if expired_keys:
                logger.info(f"Cleaned up {len(expired_keys)} expired cache entries")

            return len(expired_keys)


class PersistentToolCache(ToolCache):
    """Persistent cache that saves to disk"""

    def __init__(self, cache_file: str = "tool_cache.json", max_size: int = 1000, default_ttl: int = 3600):
        super().__init__(max_size, default_ttl)
        self.cache_file = Path(cache_file)
        self._load_cache()

    def _load_cache(self) -> None:
        """Load cache from disk"""
        if not self.cache_file.exists():
            return

        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            for key, entry_data in data.items():
                entry = CacheEntry(
                    key=key,
                    value=entry_data["value"],
                    timestamp=entry_data["timestamp"],
                    ttl=entry_data.get("ttl"),
                    access_count=entry_data.get("access_count", 0),
                    last_accessed=entry_data.get("last_accessed", entry_data["timestamp"]),
                    metadata=entry_data.get("metadata"),
                )
                self.cache[key] = entry

            logger.info(f"Loaded {len(self.cache)} cache entries from {self.cache_file}")

        except Exception as e:
            logger.error(f"Failed to load cache from {self.cache_file}: {e}")

    def _save_cache(self) -> None:
        """Save cache to disk"""
        try:
            data = {}
            for key, entry in self.cache.items():
                data[key] = {
                    "value": entry.value,
                    "timestamp": entry.timestamp,
                    "ttl": entry.ttl,
                    "access_count": entry.access_count,
                    "last_accessed": entry.last_accessed,
                    "metadata": entry.metadata,
                }

            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.error(f"Failed to save cache to {self.cache_file}: {e}")

    async def set(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        value: Any,
        ttl: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Cache result and save to disk"""
        await super().set(tool_name, parameters, value, ttl, metadata)
        self._save_cache()

    async def invalidate(self, tool_name: Optional[str] = None, parameters: Optional[Dict[str, Any]] = None) -> int:
        """Invalidate cache entries and save changes"""
        count = await super().invalidate(tool_name, parameters)
        self._save_cache()
        return count

    async def cleanup_expired(self) -> int:
        """Clean up expired entries and save changes"""
        count = await super().cleanup_expired()
        self._save_cache()
        return count


# Global cache instances
_memory_cache = ToolCache()
_persistent_cache = PersistentToolCache()


async def get_memory_cache() -> ToolCache:
    """Get memory-only cache instance"""
    return _memory_cache


async def get_persistent_cache() -> PersistentToolCache:
    """Get persistent cache instance"""
    return _persistent_cache


async def get_cache_stats() -> Dict[str, Any]:
    """Get combined cache statistics"""
    memory_stats = await _memory_cache.get_stats()
    persistent_stats = await _persistent_cache.get_stats()

    return {
        "memory_cache": memory_stats,
        "persistent_cache": persistent_stats,
        "combined": {
            "total_entries": memory_stats["total_entries"] + persistent_stats["total_entries"],
            "total_accesses": memory_stats["total_accesses"] + persistent_stats["total_accesses"],
        },
    }


async def cleanup_all_caches() -> Dict[str, int]:
    """Clean up all expired cache entries"""
    memory_cleaned = await _memory_cache.cleanup_expired()
    persistent_cleaned = await _persistent_cache.cleanup_expired()

    return {
        "memory_cache": memory_cleaned,
        "persistent_cache": persistent_cleaned,
        "total_cleaned": memory_cleaned + persistent_cleaned,
    }
