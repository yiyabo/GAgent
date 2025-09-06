#!/usr/bin/env python3
"""
GLM Embeddings Service Module (Refactored Version)

Refactored GLM Embeddings service with componentized architecture following 
Single Responsibility Principle. The main service class coordinates various 
specialized components and provides a unified public interface.
"""

import logging
from concurrent.futures import Future
from typing import Any, Callable, Dict, List, Optional

from app.services.embeddings.async_embedding_manager import AsyncEmbeddingManager
from app.services.embeddings.cache import get_embedding_cache
from app.services.foundation.config import get_config
from app.services.embeddings.embedding_batch_processor import EmbeddingBatchProcessor
from app.services.embeddings.glm_api_client import GLMApiClient
from app.services.embeddings.similarity_calculator import SimilarityCalculator

logger = logging.getLogger(__name__)


class GLMEmbeddingsService:
    """GLM Embeddings Service Class (Refactored) - Mainly responsible for coordinating components"""

    def __init__(self):
        """Initialize service and components"""
        self.config = get_config()
        self.cache = get_embedding_cache()

        # Initialize specialized components
        self.api_client = GLMApiClient(self.config)
        self.batch_processor = EmbeddingBatchProcessor(self.config, self.api_client, self.cache)
        self.async_manager = AsyncEmbeddingManager(self.batch_processor)
        self.similarity_calculator = SimilarityCalculator()

        logger.info(f"GLM Embeddings service initialized with refactored architecture")

    # Core embedding methods - delegated to BatchProcessor
    def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Get vector representations for text list (with cache support)

        Args:
            texts: List of texts

        Returns:
            List of vectors, each vector is a float list
        """
        return self.batch_processor.process_texts_batch(texts)

    def get_single_embedding(self, text: str) -> List[float]:
        """
        Get vector representation for single text

        Args:
            text: Single text

        Returns:
            Vector as float list
        """
        embeddings = self.get_embeddings([text])
        return embeddings[0] if embeddings else []

    # Async methods - delegated to AsyncEmbeddingManager
    def get_embeddings_async(self, texts: List[str], callback: Optional[Callable] = None) -> Future:
        """
        Get embeddings asynchronously

        Args:
            texts: List of texts
            callback: Optional callback function to receive embeddings result

        Returns:
            Future object
        """
        return self.async_manager.get_embeddings_async(texts, callback)

    def get_single_embedding_async(self, text: str, callback: Optional[Callable] = None) -> Future:
        """
        Get single embedding asynchronously

        Args:
            text: Single text
            callback: Optional callback function

        Returns:
            Future object
        """
        return self.async_manager.get_single_embedding_async(text, callback)

    def precompute_embeddings_async(self, texts: List[str], progress_callback: Optional[Callable] = None) -> Future:
        """
        Precompute embeddings asynchronously

        Args:
            texts: List of texts
            progress_callback: Progress callback function

        Returns:
            Future object, result contains statistics
        """
        return self.async_manager.precompute_embeddings_async(texts, progress_callback)

    def wait_for_background_tasks(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """
        Wait for all background tasks to complete

        Args:
            timeout: Timeout in seconds

        Returns:
            Task completion status
        """
        return self.async_manager.wait_for_background_tasks(timeout)

    def get_background_task_status(self) -> Dict[str, Any]:
        """
        Get background task status

        Returns:
            Dictionary containing task status information
        """
        return self.async_manager.get_async_status()

    def cancel_background_tasks(self) -> int:
        """
        Cancel all unfinished background tasks

        Returns:
            Number of successfully cancelled tasks
        """
        return self.async_manager.cancel_background_tasks()

    # Similarity calculation methods - delegated to SimilarityCalculator
    def compute_similarity(self, embedding1: List[float], embedding2: List[float]) -> float:
        """Calculate cosine similarity between two vectors"""
        return self.similarity_calculator.compute_similarity(embedding1, embedding2)

    def compute_similarities(self, query_embedding: List[float], target_embeddings: List[List[float]]) -> List[float]:
        """Calculate similarities between query vector and multiple target vectors"""
        return self.similarity_calculator.compute_similarities(query_embedding, target_embeddings)

    def find_most_similar(
        self, query_embedding: List[float], candidates: List[Dict[str, Any]], k: int = 5, min_similarity: float = 0.0
    ) -> List[Dict[str, Any]]:
        """Find most similar candidates"""
        return self.similarity_calculator.find_most_similar(query_embedding, candidates, k, min_similarity)

    # Service information and configuration methods
    def get_service_info(self) -> Dict[str, Any]:
        """Get service information"""
        return {
            "service_type": "GLMEmbeddingsService",
            "version": "2.0.0-refactored",
            "config": {
                "model": self.config.embedding_model,
                "dimension": self.config.embedding_dimension,
                "mock_mode": self.config.mock_mode,
            },
            "components": {
                "api_client": self.api_client.get_client_info(),
                "batch_processor": self.batch_processor.get_performance_stats(),
                "async_manager": self.async_manager.get_async_status(),
            },
        }

    # Compatibility methods - maintain backward compatibility
    def get_optimal_batch_size(self) -> int:
        """Get optimal batch size"""
        return self.batch_processor.get_optimal_batch_size()

    def test_connection(self) -> bool:
        """Test API connection"""
        return self.api_client.test_connection()

    def embedding_to_json(self, embedding: List[float]) -> str:
        """Convert embedding to JSON string for storage"""
        import json

        return json.dumps(embedding)

    def json_to_embedding(self, json_str: str) -> List[float]:
        """Convert JSON string back to embedding"""
        import json

        return json.loads(json_str)


# 导入线程安全版本
from app.services.embeddings.thread_safe_embeddings import (
    get_thread_safe_embeddings_service,
    shutdown_thread_safe_embeddings_service,
)


# 保持向后兼容性
def get_embeddings_service():
    """获取嵌入向量服务（线程安全版本）"""
    return get_thread_safe_embeddings_service()


def shutdown_embeddings_service():
    """关闭嵌入向量服务（线程安全版本）"""
    shutdown_thread_safe_embeddings_service()


# Singleton pattern for service instance (保留用于兼容性，但标记为已弃用)
_embeddings_service = None


class GLMEmbeddingsServiceLegacy(GLMEmbeddingsService):
    """遗留的GLM嵌入向量服务类（已弃用，建议使用线程安全版本）"""

    def __init__(self):
        import warnings

        warnings.warn(
            "GLMEmbeddingsService is deprecated and not thread-safe. "
            "Use get_thread_safe_embeddings_service() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__()
