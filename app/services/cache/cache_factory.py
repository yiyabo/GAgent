"""
Cache Factory for creating different types of cache instances.

Provides a centralized way to create and configure cache instances
with different storage backends and configurations.
"""

import hashlib
import logging
from typing import Dict, Optional, Type, Any

from .base_cache import BaseCache
from .embedding_cache import EmbeddingCache
from .llm_cache import LLMCache

logger = logging.getLogger(__name__)


class CacheFactory:
    """Factory for creating cache instances."""
    
    _registry: Dict[str, Type[BaseCache]] = {}
    _instances: Dict[str, BaseCache] = {}
    
    @classmethod
    def register_cache_type(cls, name: str, cache_class: Type[BaseCache]) -> None:
        """
        Register a new cache type.
        
        Args:
            name: Cache type name
            cache_class: Cache implementation class
        """
        cls._registry[name] = cache_class
        logger.info(f"Registered cache type: {name}")
    
    @classmethod
    def create_cache(
        cls,
        cache_type: str,
        cache_name: str,
        max_size: int = 1000,
        default_ttl: int = 3600,
        enable_persistent: bool = True,
        cleanup_interval: int = 300,
        **kwargs
    ) -> BaseCache:
        """
        Create a cache instance.
        
        Args:
            cache_type: Type of cache to create
            cache_name: Name of the cache
            max_size: Maximum number of entries
            default_ttl: Default TTL in seconds
            enable_persistent: Enable persistent storage
            cleanup_interval: Cleanup interval in seconds
            **kwargs: Additional arguments for cache class
            
        Returns:
            Cache instance
        """
        if cache_type not in cls._registry:
            raise ValueError(f"Unknown cache type: {cache_type}. Available: {list(cls._registry.keys())}")
        
        cache_class = cls._registry[cache_type]
        cache_instance = cache_class(
            cache_name=cache_name,
            max_size=max_size,
            default_ttl=default_ttl,
            enable_persistent=enable_persistent,
            cleanup_interval=cleanup_interval,
            **kwargs
        )
        
        logger.info(f"Created cache instance: {cache_name} (type: {cache_type})")
        return cache_instance
    
    @classmethod
    def get_cache(
        cls,
        cache_type: str,
        cache_name: str,
        create_if_missing: bool = True,
        **kwargs
    ) -> BaseCache:
        """
        Get a cache instance, creating it if missing.
        
        Args:
            cache_type: Type of cache
            cache_name: Name of the cache
            create_if_missing: Create cache if it doesn't exist
            **kwargs: Additional arguments for cache creation
            
        Returns:
            Cache instance
        """
        instance_key = f"{cache_type}:{cache_name}"
        
        if instance_key in cls._instances:
            return cls._instances[instance_key]
        
        if create_if_missing:
            cache_instance = cls.create_cache(cache_type, cache_name, **kwargs)
            cls._instances[instance_key] = cache_instance
            return cache_instance
        
        raise ValueError(f"Cache instance not found: {instance_key}. Set create_if_missing=True to create it.")
    
    @classmethod
    def list_cache_types(cls) -> list:
        """List all registered cache types."""
        return list(cls._registry.keys())
    
    @classmethod
    def list_cache_instances(cls) -> list:
        """List all active cache instances."""
        return list(cls._instances.keys())


# Register default cache types (will be extended by specific cache implementations)
class SimpleCache(BaseCache):
    """Simple in-memory cache implementation."""
    
    def _generate_key(self, *args, **kwargs) -> str:
        """Generate simple hash key from arguments."""
        key_data = str(args) + str(sorted(kwargs.items()))
        return hashlib.md5(key_data.encode()).hexdigest()


# Register the simple cache type
CacheFactory.register_cache_type("simple", SimpleCache)

# Register the embedding cache type
CacheFactory.register_cache_type("embedding", EmbeddingCache)

# Register the LLM cache type
CacheFactory.register_cache_type("llm", LLMCache)