"""
Embedding Cache Implementation using Unified Base Cache

Provides caching for text embeddings with model-specific caching and 
batch processing support.
"""

import hashlib
import logging
from typing import List, Optional, Tuple

from .base_cache import BaseCache, CacheEntry

logger = logging.getLogger(__name__)


class EmbeddingCache(BaseCache):
    """
    Cache for text embeddings with model-specific caching.
    
    Extends BaseCache with embedding-specific features:
    - Model-aware key generation
    - Batch processing support
    - Vector storage optimization
    """
    
    def __init__(
        self,
        cache_name: str = "embedding",
        max_size: int = 10000,
        default_ttl: int = 7200,  # 2 hours default for embeddings
        enable_persistent: bool = True,
        cleanup_interval: int = 300
    ):
        """
        Initialize embedding cache.
        
        Args:
            cache_name: Name of the cache (default: "embedding")
            max_size: Maximum number of entries in cache
            default_ttl: Default TTL in seconds (default: 7200)
            enable_persistent: Enable persistent storage
            cleanup_interval: Cleanup interval in seconds
        """
        super().__init__(
            cache_name=cache_name,
            max_size=max_size,
            default_ttl=default_ttl,
            enable_persistent=enable_persistent,
            cleanup_interval=cleanup_interval
        )
    
    def _generate_key(self, text: str, model: str = "default") -> str:
        """
        Generate cache key for text and model.
        
        Args:
            text: Text to generate embedding for
            model: Model name
            
        Returns:
            Cache key string
        """
        # Create a consistent key using text and model
        key_data = f"{text.strip().lower()}|{model}"
        return hashlib.md5(key_data.encode('utf-8')).hexdigest()
    
    def get_embedding(self, text: str, model: str = "default") -> Optional[List[float]]:
        """
        Get embedding from cache.
        
        Args:
            text: Text to get embedding for
            model: Model name
            
        Returns:
            Embedding vector or None if not cached
        """
        key = self._generate_key(text, model)
        result = self.get(key)
        
        if result is not None:
            logger.debug(f"Cache hit for embedding: {text[:50]}...")
        
        return result
    
    def set_embedding(
        self,
        text: str,
        embedding: List[float],
        model: str = "default",
        ttl: Optional[int] = None
    ) -> None:
        """
        Store embedding in cache.
        
        Args:
            text: Text that was embedded
            embedding: Embedding vector
            model: Model name
            ttl: TTL in seconds
        """
        key = self._generate_key(text, model)
        self.set(key, embedding, ttl)
        logger.debug(f"Cached embedding for: {text[:50]}...")
    
    def get_batch(
        self,
        texts: List[str],
        model: str = "default"
    ) -> Tuple[List[Optional[List[float]]], List[int]]:
        """
        Get multiple embeddings from cache.
        
        Args:
            texts: List of texts to get embeddings for
            model: Model name
            
        Returns:
            Tuple of (embeddings, missing_indices)
        """
        embeddings = []
        missing_indices = []
        
        for i, text in enumerate(texts):
            embedding = self.get_embedding(text, model)
            if embedding is not None:
                embeddings.append(embedding)
            else:
                embeddings.append(None)
                missing_indices.append(i)
        
        return embeddings, missing_indices
    
    def set_batch(
        self,
        texts: List[str],
        embeddings: List[List[float]],
        model: str = "default",
        ttl: Optional[int] = None
    ) -> List[int]:
        """
        Store multiple embeddings in cache.
        
        Args:
            texts: List of texts that were embedded
            embeddings: List of embedding vectors
            model: Model name
            ttl: TTL in seconds
            
        Returns:
            List of indices that were successfully cached
        """
        cached_indices = []
        
        if len(texts) != len(embeddings):
            logger.warning("Texts and embeddings length mismatch in batch cache operation")
            return cached_indices
        
        for i, (text, embedding) in enumerate(zip(texts, embeddings)):
            try:
                self.set_embedding(text, embedding, model, ttl)
                cached_indices.append(i)
            except Exception as e:
                logger.error(f"Failed to cache embedding for text {i}: {e}")
        
        logger.debug(f"Cached {len(cached_indices)} embeddings in batch operation")
        return cached_indices
    
    def clear_model(self, model: str) -> int:
        """
        Clear all entries for a specific model.
        
        Args:
            model: Model name to clear
            
        Returns:
            Number of entries cleared
        """
        cleared_count = 0
        
        with self._lock:
            # Find keys that match the model
            keys_to_remove = []
            for key in self._memory_cache:
                # Check if key was generated with this model
                # We can't easily reverse the key generation, so we'll use
                # the database to find matching entries
                pass
            
            if self.enable_persistent:
                try:
                    import sqlite3
                    with sqlite3.connect(self._db_path) as conn:
                        # Find entries with specific model in their data
                        cursor = conn.execute(
                            'SELECT key FROM cache_entries WHERE entry_data LIKE ?',
                            (f'%"model":"{model}"%',)
                        )
                        for row in cursor:
                            key = row[0]
                            if key in self._memory_cache:
                                del self._memory_cache[key]
                                keys_to_remove.append(key)
                        
                        # Delete from database
                        cursor.execute(
                            'DELETE FROM cache_entries WHERE entry_data LIKE ?',
                            (f'%"model":"{model}"%',)
                        )
                        conn.commit()
                        cleared_count = cursor.rowcount
                        
                except Exception as e:
                    logger.error(f"Failed to clear model {model} from database: {e}")
            
            # Remove from memory cache
            for key in keys_to_remove:
                if key in self._memory_cache:
                    del self._memory_cache[key]
                    cleared_count += 1
        
        logger.info(f"Cleared {cleared_count} entries for model: {model}")
        return cleared_count
    
    def get_model_stats(self, model: str) -> dict:
        """
        Get statistics for a specific model.
        
        Args:
            model: Model name
            
        Returns:
            Dictionary with model-specific statistics
        """
        total_entries = 0
        model_entries = 0
        
        with self._lock:
            total_entries = len(self._memory_cache)
            
            # Count model-specific entries (approximate)
            if self.enable_persistent:
                try:
                    import sqlite3
                    with sqlite3.connect(self._db_path) as conn:
                        cursor = conn.execute(
                            'SELECT COUNT(*) FROM cache_entries WHERE entry_data LIKE ?',
                            (f'%"model":"{model}"%',)
                        )
                        model_entries = cursor.fetchone()[0]
                except Exception as e:
                    logger.error(f"Failed to get model stats from database: {e}")
        
        return {
            'model': model,
            'model_entries': model_entries,
            'total_entries': total_entries,
            'model_percentage': (model_entries / total_entries * 100) if total_entries > 0 else 0,
        }


# Convenience function to get embedding cache instance
def get_embedding_cache() -> EmbeddingCache:
    """Get the default embedding cache instance."""
    from .cache_factory import CacheFactory
    return CacheFactory.get_cache("embedding", "default")


# Backward compatibility: create a singleton instance for legacy code
_legacy_embedding_cache = None

def get_embedding_cache_legacy() -> EmbeddingCache:
    """Get embedding cache instance for backward compatibility."""
    global _legacy_embedding_cache
    if _legacy_embedding_cache is None:
        _legacy_embedding_cache = EmbeddingCache()
    return _legacy_embedding_cache