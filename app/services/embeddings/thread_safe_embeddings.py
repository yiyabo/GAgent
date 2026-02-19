#!/usr/bin/env python3
"""
Thread-safe embeddings service facade.

Coordinates provider selection, batching, async execution, caching,
and similarity utilities.
"""

import logging
import threading
from concurrent.futures import Future
from typing import Any, Callable, Dict, List, Optional

from app.services.foundation.config import get_config
from app.services.embeddings.glm_api_client import GLMApiClient
from app.services.embeddings.local_embedding_client import LocalEmbeddingClient
from app.services.embeddings.similarity_calculator import SimilarityCalculator
from app.services.embeddings.thread_safe_async_manager import ThreadSafeAsyncManager
from app.services.embeddings.thread_safe_batch_processor import ThreadSafeBatchProcessor
from app.services.embeddings.thread_safe_cache import get_thread_safe_embedding_cache

logger = logging.getLogger(__name__)


class ThreadSafeEmbeddingsService:
    """Thread-safe embedding service supporting multiple providers."""

    def __init__(self):
        """Initialize provider clients and thread-safe helper components."""
        self.config = get_config()
        self.cache = get_thread_safe_embedding_cache()

        provider = getattr(self.config, 'embedding_provider', 'qwen')

        if provider == "local" or self.config.use_local_embedding:
            logger.info("Using local embedding model (thread-safe)")
            self.api_client = LocalEmbeddingClient(self.config)
            self._provider = "local"
        elif provider == "qwen":
            logger.info("Using Qwen API for embeddings (text-embedding-v4)")
            from app.services.embeddings.qwen_embedding_client import QwenEmbeddingClient
            self.api_client = QwenEmbeddingClient(self.config)
            self._provider = "qwen"
        else:  # glm or default
            logger.info("Using GLM API for embeddings (thread-safe)")
            self.api_client = GLMApiClient(self.config)
            self._provider = "glm"

        self.batch_processor = ThreadSafeBatchProcessor(self.config, self.api_client, self.cache)
        self.async_manager = ThreadSafeAsyncManager(self.batch_processor)
        self.similarity_calculator = SimilarityCalculator()

        self._service_lock = threading.RLock()

        logger.info(
            f"Thread-safe embeddings service initialized - "
            f"Provider: {self._provider}, Model: {self.config.embedding_model}, "
            f"Dimension: {self.config.embedding_dimension}"
        )

    def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Get embeddings for a list of texts.

        Args:
            texts: Input texts.

        Returns:
            Embedding vectors in the same order as input.
        """
        with self._service_lock:
            return self.batch_processor.process_texts_batch(texts)

    def get_single_embedding(self, text: str) -> List[float]:
        """
        Get embedding for a single text.

        Args:
            text: Input text.

        Returns:
            Embedding vector.
        """
        embeddings = self.get_embeddings([text])
        return embeddings[0] if embeddings else []

    def get_embeddings_async(self, texts: List[str], callback: Optional[Callable] = None) -> Future:
        """
        Submit async request for multiple embeddings.

        Args:
            texts: Input texts.
            callback: Optional callback for results.

        Returns:
            Future resolving to `List[List[float]]`.
        """
        return self.async_manager.get_embeddings_async(texts, callback)

    def get_single_embedding_async(self, text: str, callback: Optional[Callable] = None) -> Future:
        """
        Submit async request for one embedding.

        Args:
            text: Input text.
            callback: Optional callback for result.

        Returns:
            Future resolving to `List[float]`.
        """
        return self.async_manager.get_single_embedding_async(text, callback)

    def precompute_embeddings_async(self, texts: List[str], progress_callback: Optional[Callable] = None) -> Future:
        """
        Submit async precompute request for a text batch.

        Args:
            texts: Input texts.
            progress_callback: Optional progress callback.

        Returns:
            Future resolving to precompute statistics.
        """
        return self.async_manager.precompute_embeddings_async(texts, progress_callback)

    def wait_for_background_tasks(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """
        Wait for active async tasks to finish.

        Args:
            timeout: Optional timeout in seconds.

        Returns:
            Completion summary.
        """
        return self.async_manager.wait_for_background_tasks(timeout)

    def get_background_task_status(self) -> Dict[str, Any]:
        """
        Get current async background task status.

        Returns:
            Status dictionary with counters and metrics.
        """
        return self.async_manager.get_async_status()

    def cancel_background_tasks(self) -> int:
        """
        Cancel currently active background tasks.

        Returns:
            Number of successfully cancelled tasks.
        """
        return self.async_manager.cancel_background_tasks()

    def compute_similarity(self, embedding1: List[float], embedding2: List[float]) -> float:
        """Compute cosine similarity between two embeddings."""
        return self.similarity_calculator.compute_similarity(embedding1, embedding2)

    def compute_similarities(self, query_embedding: List[float], target_embeddings: List[List[float]]) -> List[float]:
        """Compute cosine similarities from one query to many targets."""
        return self.similarity_calculator.compute_similarities(query_embedding, target_embeddings)

    def find_most_similar(
        self, query_embedding: List[float], candidates: List[Dict[str, Any]], k: int = 5, min_similarity: float = 0.0
    ) -> List[Dict[str, Any]]:
        """Return top-k most similar candidates above threshold."""
        return self.similarity_calculator.find_most_similar(query_embedding, candidates, k, min_similarity)

    def get_service_info(self) -> Dict[str, Any]:
        """Return service metadata and component status."""
        with self._service_lock:
            return {
                "service_type": "ThreadSafeEmbeddingsService",
                "version": "3.0.0-thread-safe",
                "thread_safe": True,
                "config": {
                    "model": self.config.embedding_model,
                    "dimension": self.config.embedding_dimension,
                    "mock_mode": self.config.mock_mode,
                },
                "components": {
                    "api_client": self.api_client.get_client_info(),
                    "batch_processor": self.batch_processor.get_performance_stats(),
                    "async_manager": self.async_manager.get_async_status(),
                    "cache": self.cache.get_stats(),
                },
            }

    def get_optimal_batch_size(self) -> int:
        """Return current optimal batch size from batch processor."""
        return self.batch_processor.get_optimal_batch_size()

    def test_connection(self) -> bool:
        """Check embedding provider connectivity."""
        return self.api_client.test_connection()

    def embedding_to_json(self, embedding: List[float]) -> str:
        """Serialize embedding vector to JSON string."""
        import json

        return json.dumps(embedding)

    def json_to_embedding(self, json_str: str) -> List[float]:
        """Deserialize embedding vector from JSON string."""
        import json

        return json.loads(json_str)

    def precompute_embeddings_for_completed_tasks(self, batch_size: int = 10) -> int:
        """Precompute embeddings for completed tasks in storage."""
        return self.batch_processor.precompute_for_completed_tasks(batch_size)

    def shutdown(self) -> None:
        """Shut down async manager and cache resources."""
        with self._service_lock:
            logger.info("Shutting down thread-safe embeddings service")
            self.async_manager.shutdown()
            self.cache.shutdown()


_thread_safe_service: Optional[ThreadSafeEmbeddingsService] = None
_service_creation_lock = threading.Lock()


def get_thread_safe_embeddings_service() -> ThreadSafeEmbeddingsService:
    """Get singleton instance of thread-safe embeddings service."""
    global _thread_safe_service

    if _thread_safe_service is None:
        with _service_creation_lock:
            if _thread_safe_service is None:
                _thread_safe_service = ThreadSafeEmbeddingsService()

    return _thread_safe_service


def shutdown_thread_safe_embeddings_service():
    """Shutdown and clear singleton service instance."""
    global _thread_safe_service

    with _service_creation_lock:
        if _thread_safe_service is not None:
            _thread_safe_service.shutdown()
            _thread_safe_service = None


def get_embeddings_service() -> ThreadSafeEmbeddingsService:
    """Backward-compatible accessor for thread-safe embeddings service."""
    return get_thread_safe_embeddings_service()


def shutdown_embeddings_service():
    """Backward-compatible shutdown wrapper."""
    shutdown_thread_safe_embeddings_service()
