#!/usr/bin/env python3
"""
. 

medium, : 
1. 
2. update
3. API
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ThreadSafeBatchProcessor:
    """"""

    def __init__(self, config, api_client, cache):
        """


        Args:
            config: configuration
            api_client: API
            cache: 
        """
        self.config = config
        self.api_client = api_client
        self.cache = cache

        self._stats_lock = threading.RLock()
        self._batch_count = 0
        self._total_texts = 0
        self._cache_hits = 0
        self._api_calls = 0
        self._total_time = 0.0

        self._batch_size_lock = threading.RLock()
        self._current_batch_size = getattr(config, "embedding_batch_size", 10)
        self._performance_history = []
        self._last_adjustment_time = 0

        self._active_requests = {}  # text_hash -> Future
        self._request_lock = threading.RLock()

        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="batch-processor")

        logger.info("Thread-safe batch processor initialized")

    def process_texts_batch(self, texts: List[str]) -> List[List[float]]:
        """


        Args:
            texts: 

        Returns:

        """
        if not texts:
            return []

        start_time = time.time()

        try:
            cached_results, cache_misses = self.cache.get_batch(texts, self.api_client.model)

            with self._stats_lock:
                self._cache_hits += len(texts) - len(cache_misses)

            if cache_misses:
                miss_texts = [texts[i] for i in cache_misses]
                new_embeddings = self._compute_embeddings_with_deduplication(miss_texts)

                if new_embeddings:
                    self._update_cache_atomic(miss_texts, new_embeddings)

                for i, miss_idx in enumerate(cache_misses):
                    if i < len(new_embeddings):
                        cached_results[miss_idx] = new_embeddings[i]

            processing_time = time.time() - start_time
            self._update_performance_stats(len(texts), len(cache_misses), processing_time)

            return [result for result in cached_results if result is not None]

        except Exception as e:
            logger.error(f"Batch processing failed: {e}")
            return [[] for _ in texts]

    def _compute_embeddings_with_deduplication(self, texts: List[str]) -> List[List[float]]:
        """


        Args:
            texts: 

        Returns:

        """
        unique_texts = []
        text_to_indices = {}

        for i, text in enumerate(texts):
            text_hash = self._compute_text_hash(text)
            if text_hash not in text_to_indices:
                text_to_indices[text_hash] = []
                unique_texts.append(text)
            text_to_indices[text_hash].append(i)

        pending_requests = []
        texts_to_compute = []

        with self._request_lock:
            for text in unique_texts:
                text_hash = self._compute_text_hash(text)
                if text_hash in self._active_requests:
                    pending_requests.append((text, self._active_requests[text_hash]))
                else:
                    texts_to_compute.append(text)

        embeddings_map = {}
        for text, future in pending_requests:
            try:
                embeddings_map[text] = future.result(timeout=30)
            except Exception as e:
                logger.warning(f"Waiting for existing request failed: {e}")
                texts_to_compute.append(text)

        if texts_to_compute:
            new_embeddings = self._compute_embeddings_batch(texts_to_compute)
            for i, text in enumerate(texts_to_compute):
                if i < len(new_embeddings):
                    embeddings_map[text] = new_embeddings[i]

        results = []
        for text in texts:
            if text in embeddings_map:
                results.append(embeddings_map[text])
            else:
                results.append([])

        return results

    def _compute_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        """
        (please)

        Args:
            texts: 

        Returns:

        """
        if not texts:
            return []

        futures_map = {}
        with self._request_lock:
            for text in texts:
                text_hash = self._compute_text_hash(text)
                if text_hash not in self._active_requests:
                    future = self._executor.submit(self._single_api_call, [text])
                    self._active_requests[text_hash] = future
                    futures_map[text] = future

        try:
            batch_size = self._get_optimal_batch_size()

            results = []
            for i in range(0, len(texts), batch_size):
                batch_texts = texts[i : i + batch_size]
                try:
                    batch_embeddings = self.api_client.get_embeddings(batch_texts)
                    results.extend(batch_embeddings)

                    with self._stats_lock:
                        self._api_calls += 1

                except Exception as e:
                    logger.error(f"API call failed for batch {i//batch_size + 1}: {e}")
                    results.extend([[] for _ in batch_texts])

            return results

        finally:
            with self._request_lock:
                for text in texts:
                    text_hash = self._compute_text_hash(text)
                    self._active_requests.pop(text_hash, None)

    def _single_api_call(self, texts: List[str]) -> List[List[float]]:
        """API(please)"""
        return self.api_client.get_embeddings(texts)

    def _update_cache_atomic(self, texts: List[str], embeddings: List[List[float]]) -> None:
        """update"""
        if len(texts) != len(embeddings):
            logger.warning(f"Texts and embeddings length mismatch: {len(texts)} vs {len(embeddings)}")
            return

        try:
            self.cache.put_batch(texts, embeddings, self.api_client.model)
        except Exception as e:
            logger.error(f"Failed to update cache: {e}")

    def _update_performance_stats(self, total_texts: int, cache_misses: int, processing_time: float):
        """update()"""
        with self._stats_lock:
            self._batch_count += 1
            self._total_texts += total_texts
            self._total_time += processing_time

            if cache_misses > 0:  # API
                throughput = cache_misses / processing_time if processing_time > 0 else 0
                with self._batch_size_lock:
                    self._performance_history.append(
                        {"batch_size": self._current_batch_size, "throughput": throughput, "timestamp": time.time()}
                    )

                    if len(self._performance_history) > 10:
                        self._performance_history.pop(0)

                    self._maybe_adjust_batch_size()

    def _maybe_adjust_batch_size(self):
        """()"""
        current_time = time.time()

        if current_time - self._last_adjustment_time < 60:
            return

        if len(self._performance_history) < 3:
            return

        recent_throughputs = [record["throughput"] for record in self._performance_history[-3:]]
        avg_throughput = sum(recent_throughputs) / len(recent_throughputs)

        if avg_throughput > 0:
            if all(t >= avg_throughput * 0.9 for t in recent_throughputs):
                if self._current_batch_size < 50:
                    self._current_batch_size = min(self._current_batch_size + 5, 50)
                    logger.info(f"Increased batch size to {self._current_batch_size}")
            elif avg_throughput < recent_throughputs[0] * 0.8:
                if self._current_batch_size > 5:
                    self._current_batch_size = max(self._current_batch_size - 2, 5)
                    logger.info(f"Decreased batch size to {self._current_batch_size}")

        self._last_adjustment_time = current_time

    def _get_optimal_batch_size(self) -> int:
        """get()"""
        with self._batch_size_lock:
            return self._current_batch_size

    def get_optimal_batch_size(self) -> int:
        """: get"""
        return self._get_optimal_batch_size()

    def _compute_text_hash(self, text: str) -> str:
        """"""
        import hashlib

        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def get_performance_stats(self) -> Dict[str, Any]:
        """get()"""
        with self._stats_lock:
            avg_time = self._total_time / self._batch_count if self._batch_count > 0 else 0
            cache_hit_rate = self._cache_hits / self._total_texts if self._total_texts > 0 else 0

            return {
                "batch_count": self._batch_count,
                "total_texts_processed": self._total_texts,
                "cache_hits": self._cache_hits,
                "cache_hit_rate": cache_hit_rate,
                "api_calls": self._api_calls,
                "total_processing_time": self._total_time,
                "average_batch_time": avg_time,
                "current_batch_size": self._current_batch_size,
                "thread_safe": True,
            }

    def precompute_for_completed_tasks(self, batch_size: int = 10) -> int:
        """completedtask"""
        try:
            from ..database import init_db
            from ..repository.tasks import SqliteTaskRepository

            init_db()
            repo = SqliteTaskRepository()

            completed_tasks = repo.list_all_tasks()
            completed_tasks = [t for t in completed_tasks if t.get("status") == "completed"]

            if not completed_tasks:
                return 0

            texts = []
            for task in completed_tasks:
                content = task.get("content", "")
                if content and content.strip():
                    texts.append(content.strip())

            if not texts:
                return 0

            processed_count = 0
            for i in range(0, len(texts), batch_size):
                batch_texts = texts[i : i + batch_size]
                self.process_texts_batch(batch_texts)
                processed_count += len(batch_texts)
                logger.info(f"Precomputed embeddings for {processed_count}/{len(texts)} tasks")

            return processed_count

        except Exception as e:
            logger.error(f"Failed to precompute embeddings for completed tasks: {e}")
            return 0

    def shutdown(self):
        """close"""
        logger.info("Shutting down thread-safe batch processor")
        self._executor.shutdown(wait=True)
