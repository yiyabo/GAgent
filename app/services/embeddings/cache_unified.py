"""
New Embedding Cache using Unified Base Cache

This is a temporary file to bridge the new cache system with existing code.
"""

from ..cache.embedding_cache import EmbeddingCache

# Re-export for compatibility
get_embedding_cache = EmbeddingCache