"""Embeddings services and utilities."""

from .embeddings import get_embeddings_service, shutdown_embeddings_service

__all__ = [
    "get_embeddings_service",
    "shutdown_embeddings_service",
]

 