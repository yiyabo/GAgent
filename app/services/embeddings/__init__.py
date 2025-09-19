"""Embeddings services and utilities."""

from .embeddings import get_embeddings_service, shutdown_embeddings_service
from .vector_adapter import VectorStorageAdapter, get_vector_adapter, migrate_embeddings_service

__all__ = [
    "get_embeddings_service",
    "shutdown_embeddings_service",
    "VectorStorageAdapter",
    "get_vector_adapter", 
    "migrate_embeddings_service",
]

