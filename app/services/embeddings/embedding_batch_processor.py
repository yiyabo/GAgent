#!/usr/bin/env python3
"""
Embedding Batch Processor Module.

Specialized in text preprocessing, deduplication, batch size optimization and concurrent batch processing management.
Extracted from GLMEmbeddingsService to follow the single responsibility principle.
"""

import logging
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Set, Tuple

logger = logging.getLogger(__name__)


class EmbeddingBatchProcessor:
    """Embedding batch processor class specialized in batch processing optimization"""

    def __init__(self, config, api_client, cache):
        """
        Initialize batch processor

        Args:
            config: Configuration object
            api_client: API client instance
            cache: Cache instance
        """
        self.config = config
        self.api_client = api_client
        self.cache = cache

        self.max_batch_size = config.max_batch_size
        self.max_concurrent_batches = 3
        self.dynamic_batch_size = self.max_batch_size

        # Performance statistics
        self.performance_stats = defaultdict(list)
        self._stats_lock = threading.Lock()

        logger.info(f"Batch processor initialized - Max batch size: {self.max_batch_size}")

    def process_texts_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Process texts in batch to get embeddings

        Args:
            texts: List of texts

        Returns:
            List of embeddings
        """
        if not texts:
            return []

        # Preprocess texts
        processed_texts = self._preprocess_texts(texts)

        # Check cache
        cached_results, cache_misses = self.cache.get_batch(processed_texts, self.api_client.model)

        if not cache_misses:
            logger.debug(f"All {len(texts)} embeddings found in cache")
            return [result for result in cached_results if result is not None]

        # Process cache miss texts
        miss_texts = [processed_texts[i] for i in cache_misses]
        logger.debug(f"Cache miss for {len(miss_texts)} texts, fetching from API")

        # Batch get embeddings
        new_embeddings = self._compute_embeddings_batch(miss_texts)

        # Update cache
        self._update_cache(miss_texts, new_embeddings)

        # Merge results
        return self._merge_results(cached_results, cache_misses, new_embeddings)

    def _preprocess_texts(self, texts: List[str]) -> List[str]:
        """Preprocess text list"""
        processed = []
        for text in texts:
            if isinstance(text, str):
                # Clean text
                cleaned = text.strip()
                if cleaned:
                    processed.append(cleaned)
                else:
                    processed.append("")  # Maintain index correspondence
            else:
                processed.append(str(text))

        return processed

    def _compute_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        """Compute embeddings batch processing"""
        if len(texts) <= self.dynamic_batch_size:
            return self._get_embeddings_single_batch(texts)
        else:
            return self._compute_embeddings_concurrent(texts)

    def _get_embeddings_single_batch(self, texts: List[str]) -> List[List[float]]:
        """Get embeddings for single batch"""
        start_time = time.time()

        try:
            embeddings = self.api_client.get_embeddings_from_api(texts)
            timing = time.time() - start_time

            # Update performance statistics
            self._update_performance_stats(len(texts), timing, True)

            # Dynamically adjust batch size
            self._adjust_batch_size(len(texts), timing, True)

            return embeddings

        except Exception as e:
            timing = time.time() - start_time
            self._update_performance_stats(len(texts), timing, False)
            self._adjust_batch_size(len(texts), timing, False)
            raise e

    def _compute_embeddings_concurrent(self, texts: List[str]) -> List[List[float]]:
        """Concurrent computation of embeddings"""
        batches = self._split_into_batches(texts)
        logger.debug(f"Split {len(texts)} texts into {len(batches)} batches")

        results = [None] * len(batches)

        with ThreadPoolExecutor(max_workers=self.max_concurrent_batches) as executor:
            future_to_index = {
                executor.submit(self._get_embeddings_single_batch, batch): i for i, batch in enumerate(batches)
            }

            for future in as_completed(future_to_index):
                batch_index = future_to_index[future]
                try:
                    batch_embeddings = future.result()
                    results[batch_index] = batch_embeddings
                except Exception as e:
                    logger.error(f"Batch {batch_index} failed: {e}")
                    raise e

        # Merge all batch results
        all_embeddings = []
        for batch_result in results:
            if batch_result:
                all_embeddings.extend(batch_result)

        return all_embeddings

    def _split_into_batches(self, texts: List[str]) -> List[List[str]]:
        """Split text list into batches"""
        batches = []
        for i in range(0, len(texts), self.dynamic_batch_size):
            batch = texts[i : i + self.dynamic_batch_size]
            batches.append(batch)
        return batches

    def _update_cache(self, texts: List[str], embeddings: List[List[float]]):
        """Update cache"""
        if len(texts) == len(embeddings):
            for text, embedding in zip(texts, embeddings):
                self.cache.put(text, embedding, self.api_client.model)

    def _merge_results(
        self, cached_results: List, cache_misses: List[int], new_embeddings: List[List[float]]
    ) -> List[List[float]]:
        """Merge cache results and newly obtained embeddings"""
        result = cached_results.copy()

        for i, miss_index in enumerate(cache_misses):
            if i < len(new_embeddings):
                result[miss_index] = new_embeddings[i]

        return [emb for emb in result if emb is not None]

    def _update_performance_stats(self, batch_size: int, timing: float, success: bool):
        """Update performance statistics"""
        with self._stats_lock:
            self.performance_stats["batch_sizes"].append(batch_size)
            self.performance_stats["timings"].append(timing)
            self.performance_stats["success_rates"].append(1 if success else 0)

            # Keep statistics data within reasonable range
            max_stats = 100
            for key in self.performance_stats:
                if len(self.performance_stats[key]) > max_stats:
                    self.performance_stats[key] = self.performance_stats[key][-max_stats:]

    def _adjust_batch_size(self, batch_size: int, timing: float, success: bool):
        """Dynamically adjust batch size"""
        if not success:
            # Reduce batch size on failure
            self.dynamic_batch_size = max(1, int(self.dynamic_batch_size * 0.8))
            logger.debug(f"Reduced batch size to {self.dynamic_batch_size} due to failure")
        else:
            # Adjust based on performance when successful
            throughput = batch_size / timing if timing > 0 else 0

            if throughput > 50 and self.dynamic_batch_size < self.max_batch_size:
                # Increase batch size on high throughput
                self.dynamic_batch_size = min(self.max_batch_size, int(self.dynamic_batch_size * 1.1))
                logger.debug(f"Increased batch size to {self.dynamic_batch_size}")
            elif throughput < 10 and self.dynamic_batch_size > 1:
                # Decrease batch size on low throughput
                self.dynamic_batch_size = max(1, int(self.dynamic_batch_size * 0.9))
                logger.debug(f"Decreased batch size to {self.dynamic_batch_size}")

    def get_optimal_batch_size(self) -> int:
        """Get current optimal batch size"""
        return self.dynamic_batch_size

    def get_performance_stats(self) -> Dict:
        """Get performance statistics information"""
        with self._stats_lock:
            if not self.performance_stats["timings"]:
                return {"message": "No performance data available"}

            timings = self.performance_stats["timings"]
            batch_sizes = self.performance_stats["batch_sizes"]
            success_rates = self.performance_stats["success_rates"]

            return {
                "current_batch_size": self.dynamic_batch_size,
                "max_batch_size": self.max_batch_size,
                "avg_timing": sum(timings) / len(timings),
                "avg_batch_size": sum(batch_sizes) / len(batch_sizes),
                "success_rate": sum(success_rates) / len(success_rates),
                "total_requests": len(timings),
            }
