#!/usr/bin/env python3
"""
Thread-safe async manager for embedding tasks.

Provides:
1. Background execution for embedding requests.
2. Safe callback invocation.
3. Task lifecycle tracking and metrics.
4. Periodic cleanup of completed tasks.
"""

import logging
import threading
import time
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class ThreadSafeAsyncManager:
    """Manage asynchronous embedding jobs with thread-safe bookkeeping."""

    def __init__(self, batch_processor):
        """
        Initialize async manager.

        Args:
            batch_processor: Thread-safe batch processor used to compute embeddings.
        """
        self.batch_processor = batch_processor

        self._async_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="embedding-async-safe")

        self._tasks_lock = threading.RLock()
        self._background_tasks = []  # Active and historical futures.
        self._task_callbacks = {}  # task_id -> callback
        self._task_metadata = {}  # task_id -> metadata

        self._stats_lock = threading.RLock()
        self._task_counters = defaultdict(int)  # Lifetime counters per task type.
        self._completion_times = []  # Sliding window of completion times.

        self._cleanup_thread = None
        self._cleanup_interval = 300  # 5 minutes
        self._shutdown_event = threading.Event()
        self._start_cleanup_thread()

        logger.info("Thread-safe async embedding manager initialized")

    def _start_cleanup_thread(self):
        """Start periodic cleanup thread."""

        def cleanup_worker():
            while not self._shutdown_event.wait(self._cleanup_interval):
                try:
                    self._cleanup_completed_tasks()
                except Exception as e:
                    logger.error(f"Cleanup thread error: {e}")

        self._cleanup_thread = threading.Thread(target=cleanup_worker, name="embedding-cleanup", daemon=True)
        self._cleanup_thread.start()

    def get_embeddings_async(self, texts: List[str], callback: Optional[Callable] = None) -> Future:
        """
        Submit async request for multiple embeddings.

        Args:
            texts: Input texts.
            callback: Optional callback invoked with computed embeddings.

        Returns:
            Future resolving to `List[List[float]]`.
        """
        if not texts:
            future = Future()
            future.set_result([])
            return future

        future = self._async_executor.submit(self._async_embedding_worker, texts, callback, task_type="get_embeddings")

        with self._tasks_lock:
            task_id = id(future)
            self._background_tasks.append(future)
            self._task_metadata[task_id] = {
                "type": "get_embeddings",
                "text_count": len(texts),
                "created_at": time.time(),
                "callback": callback is not None,
            }

            if callback:
                self._task_callbacks[task_id] = callback

        with self._stats_lock:
            self._task_counters["get_embeddings"] += 1

        logger.debug(f"Submitted async embedding task for {len(texts)} texts")
        return future

    def get_single_embedding_async(self, text: str, callback: Optional[Callable] = None) -> Future:
        """
        Submit async request for a single embedding.

        Args:
            text: Input text.
            callback: Optional callback invoked with one embedding.

        Returns:
            Future resolving to `List[float]`.
        """
        if not text.strip():
            future = Future()
            future.set_result([])
            return future

        future = self._async_executor.submit(
            self._async_single_embedding_worker, text, callback, task_type="get_single_embedding"
        )

        with self._tasks_lock:
            task_id = id(future)
            self._background_tasks.append(future)
            self._task_metadata[task_id] = {
                "type": "get_single_embedding",
                "text_count": 1,
                "created_at": time.time(),
                "callback": callback is not None,
            }

            if callback:
                self._task_callbacks[task_id] = callback

        with self._stats_lock:
            self._task_counters["get_single_embedding"] += 1

        logger.debug("Submitted async single embedding task")
        return future

    def precompute_embeddings_async(self, texts: List[str], progress_callback: Optional[Callable] = None) -> Future:
        """
        Submit async precompute request for a text batch.

        Args:
            texts: Input texts.
            progress_callback: Optional progress callback.

        Returns:
            Future resolving to precompute statistics.
        """
        if not texts:
            future = Future()
            future.set_result({"total": 0, "processed": 0, "cached": 0, "new_computed": 0, "errors": []})
            return future

        future = self._async_executor.submit(
            self._async_precompute_worker, texts, progress_callback, task_type="precompute_embeddings"
        )

        with self._tasks_lock:
            task_id = id(future)
            self._background_tasks.append(future)
            self._task_metadata[task_id] = {
                "type": "precompute_embeddings",
                "text_count": len(texts),
                "created_at": time.time(),
                "callback": progress_callback is not None,
            }

            if progress_callback:
                self._task_callbacks[task_id] = progress_callback

        with self._stats_lock:
            self._task_counters["precompute_embeddings"] += 1

        logger.debug(f"Submitted async precompute task for {len(texts)} texts")
        return future

    def _async_embedding_worker(
        self, texts: List[str], callback: Optional[Callable] = None, task_type: str = None
    ) -> List[List[float]]:
        """Worker for multi-text embedding generation."""
        start_time = time.time()
        try:
            embeddings = self.batch_processor.process_texts_batch(texts)

            if callback:
                self._safe_execute_callback(callback, embeddings)

            completion_time = time.time() - start_time
            with self._stats_lock:
                self._completion_times.append(completion_time)
                if len(self._completion_times) > 100:  # Keep a bounded window.
                    self._completion_times.pop(0)

            return embeddings

        except Exception as e:
            logger.error(f"Async embedding generation failed: {e}")

            if callback:
                self._safe_execute_callback(callback, [], error=e)

            return []

    def _async_single_embedding_worker(
        self, text: str, callback: Optional[Callable] = None, task_type: str = None
    ) -> List[float]:
        """Worker for single-text embedding generation."""
        start_time = time.time()
        try:
            embeddings = self.batch_processor.process_texts_batch([text])
            result = embeddings[0] if embeddings else []

            if callback:
                self._safe_execute_callback(callback, result)

            completion_time = time.time() - start_time
            with self._stats_lock:
                self._completion_times.append(completion_time)
                if len(self._completion_times) > 100:
                    self._completion_times.pop(0)

            return result

        except Exception as e:
            logger.error(f"Async single embedding generation failed: {e}")

            if callback:
                self._safe_execute_callback(callback, [], error=e)

            return []

    def _async_precompute_worker(
        self, texts: List[str], progress_callback: Optional[Callable] = None, task_type: str = None
    ) -> Dict[str, Any]:
        """Worker for precomputing embeddings with progress reporting."""
        start_time = time.time()
        try:
            total_texts = len(texts)
            processed_count = 0
            batch_size = self.batch_processor.get_optimal_batch_size()

            results = {"total": total_texts, "processed": 0, "cached": 0, "new_computed": 0, "errors": []}

            for i in range(0, total_texts, batch_size):
                batch_texts = texts[i : i + batch_size]

                try:
                    cached_results, cache_misses = self.batch_processor.cache.get_batch(
                        batch_texts, self.batch_processor.api_client.model
                    )

                    results["cached"] += len(batch_texts) - len(cache_misses)

                    if cache_misses:
                        miss_texts = [batch_texts[j] for j in cache_misses]
                        embeddings = self.batch_processor._compute_embeddings_batch(miss_texts)

                        self.batch_processor._update_cache_atomic(miss_texts, embeddings)
                        results["new_computed"] += len(embeddings)

                    processed_count += len(batch_texts)
                    results["processed"] = processed_count

                    if progress_callback:
                        self._safe_execute_callback(progress_callback, processed_count, total_texts, results)

                except Exception as e:
                    error_msg = f"Batch {i//batch_size + 1} failed: {e}"
                    results["errors"].append(error_msg)
                    logger.error(error_msg)

            completion_time = time.time() - start_time
            with self._stats_lock:
                self._completion_times.append(completion_time)
                if len(self._completion_times) > 100:
                    self._completion_times.pop(0)

            logger.info(f"Precompute completed: {results}")
            return results

        except Exception as e:
            logger.error(f"Async precompute failed: {e}")
            return {"error": str(e)}

    def _safe_execute_callback(self, callback: Callable, *args, error: Exception = None):
        """Execute callback safely without crashing worker threads."""
        try:
            if error:
                import inspect

                sig = inspect.signature(callback)
                if "error" in sig.parameters:
                    callback(*args, error=error)
                else:
                    callback(*args)
            else:
                callback(*args)
        except Exception as e:
            logger.warning(f"Callback execution failed: {e}")

    def wait_for_background_tasks(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """
        Wait for currently active background tasks.

        Args:
            timeout: Optional timeout in seconds.

        Returns:
            Completion summary for tasks observed during this call.
        """
        with self._tasks_lock:
            active_tasks = [task for task in self._background_tasks if not task.done()]

        if not active_tasks:
            return {"status": "no_active_tasks", "completed": 0, "failed": 0, "cancelled": 0}

        completed = 0
        failed = 0
        cancelled = 0

        try:
            for future in as_completed(active_tasks, timeout=timeout):
                try:
                    future.result()  # Raises if worker failed.
                    completed += 1
                except Exception as e:
                    logger.error(f"Background task failed: {e}")
                    failed += 1

        except TimeoutError:
            for task in active_tasks:
                if not task.done():
                    if task.cancel():
                        cancelled += 1

        self._cleanup_completed_tasks()

        return {
            "status": "completed",
            "completed": completed,
            "failed": failed,
            "cancelled": cancelled,
            "total": len(active_tasks),
        }

    def cancel_background_tasks(self) -> int:
        """
        Cancel all currently active background tasks.

        Returns:
            Number of tasks successfully cancelled.
        """
        cancelled_count = 0

        with self._tasks_lock:
            for task in self._background_tasks:
                if not task.done() and task.cancel():
                    cancelled_count += 1

        logger.info(f"Cancelled {cancelled_count} background tasks")
        return cancelled_count

    def _cleanup_completed_tasks(self):
        """Remove completed tasks and related metadata from in-memory tracking."""
        with self._tasks_lock:
            before_count = len(self._background_tasks)
            self._background_tasks = [task for task in self._background_tasks if not task.done()]
            after_count = len(self._background_tasks)

            active_task_ids = {id(task) for task in self._background_tasks}

            removed_metadata = [task_id for task_id in self._task_metadata.keys() if task_id not in active_task_ids]
            for task_id in removed_metadata:
                del self._task_metadata[task_id]

            removed_callbacks = [task_id for task_id in self._task_callbacks.keys() if task_id not in active_task_ids]
            for task_id in removed_callbacks:
                del self._task_callbacks[task_id]

        cleaned_count = before_count - after_count
        if cleaned_count > 0:
            logger.debug(f"Cleaned up {cleaned_count} completed tasks")

    def get_async_status(self) -> Dict[str, Any]:
        """Return current async execution status and performance metrics."""
        with self._tasks_lock:
            total_tasks = len(self._background_tasks)
            active_tasks = sum(1 for task in self._background_tasks if not task.done())
            completed_tasks = sum(1 for task in self._background_tasks if task.done() and not task.cancelled())
            failed_tasks = sum(
                1 for task in self._background_tasks if task.done() and not task.cancelled() and task.exception()
            )
            cancelled_tasks = sum(1 for task in self._background_tasks if task.cancelled())

            task_type_distribution = defaultdict(int)
            for task_id, metadata in self._task_metadata.items():
                task_type_distribution[metadata["type"]] += 1

        with self._stats_lock:
            avg_completion_time = (
                sum(self._completion_times) / len(self._completion_times) if self._completion_times else 0
            )
            task_counters = dict(self._task_counters)

        return {
            "total_tasks": total_tasks,
            "active_tasks": active_tasks,
            "completed_tasks": completed_tasks,
            "failed_tasks": failed_tasks,
            "cancelled_tasks": cancelled_tasks,
            "task_type_distribution": dict(task_type_distribution),
            "lifetime_task_counts": task_counters,
            "average_completion_time": avg_completion_time,
            "executor_info": {
                "max_workers": self._async_executor._max_workers,
                "thread_name_prefix": self._async_executor._thread_name_prefix,
            },
            "thread_safe": True,
        }

    def shutdown(self, wait: bool = True):
        """Shut down cleanup thread and async executor."""
        logger.info("Shutting down thread-safe async embedding manager")

        self._shutdown_event.set()

        if self._cleanup_thread and self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=5.0)

        self._async_executor.shutdown(wait=wait)

        with self._tasks_lock:
            self._background_tasks.clear()
            self._task_callbacks.clear()
            self._task_metadata.clear()
