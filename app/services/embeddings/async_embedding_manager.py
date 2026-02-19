#!/usr/bin/env python3
"""
Embedding. 

background task, progress, taskcancelstatus. 
GLMEmbeddingsServicemedium, . 
"""

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class AsyncEmbeddingManager:
    """Embedding, """

    def __init__(self, batch_processor):
        """


        Args:
            batch_processor: 
        """
        self.batch_processor = batch_processor

        self._async_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="embedding-async")
        self._background_tasks = []  # background task
        self._task_callbacks = {}  # task
        self._task_lock = threading.Lock()

        logger.info("Async embedding manager initialized")

    def get_embeddings_async(self, texts: List[str], callback: Optional[Callable] = None) -> Future:
        """
        getembeddings

        Args:
            texts: 
            callback: 

        Returns:
            Future
        """
        future = self._async_executor.submit(self._async_embedding_worker, texts, callback)

        with self._task_lock:
            self._background_tasks.append(future)
            if callback:
                self._task_callbacks[id(future)] = callback

        logger.debug(f"Submitted async embedding task for {len(texts)} texts")
        return future

    def get_single_embedding_async(self, text: str, callback: Optional[Callable] = None) -> Future:
        """
        getembedding

        Args:
            text: 
            callback: 

        Returns:
            Future
        """
        future = self._async_executor.submit(self._async_single_embedding_worker, text, callback)

        with self._task_lock:
            self._background_tasks.append(future)
            if callback:
                self._task_callbacks[id(future)] = callback

        logger.debug(f"Submitted async single embedding task")
        return future

    def precompute_embeddings_async(self, texts: List[str], progress_callback: Optional[Callable] = None) -> Future:
        """
        embeddings

        Args:
            texts: 
            progress_callback: progress

        Returns:
            Future
        """
        future = self._async_executor.submit(self._async_precompute_worker, texts, progress_callback)

        with self._task_lock:
            self._background_tasks.append(future)
            if progress_callback:
                self._task_callbacks[id(future)] = progress_callback

        logger.debug(f"Submitted async precompute task for {len(texts)} texts")
        return future

    def _async_embedding_worker(self, texts: List[str], callback: Optional[Callable] = None) -> List[List[float]]:
        """embedding"""
        try:
            embeddings = self.batch_processor.process_texts_batch(texts)

            if callback:
                try:
                    callback(embeddings)
                except Exception as e:
                    logger.warning(f"Callback execution failed: {e}")

            return embeddings

        except Exception as e:
            logger.error(f"Async embedding generation failed: {e}")
            return []

    def _async_single_embedding_worker(self, text: str, callback: Optional[Callable] = None) -> List[float]:
        """embedding"""
        try:
            embeddings = self.batch_processor.process_texts_batch([text])
            result = embeddings[0] if embeddings else []

            if callback:
                try:
                    callback(result)
                except Exception as e:
                    logger.warning(f"Callback execution failed: {e}")

            return result

        except Exception as e:
            logger.error(f"Async single embedding generation failed: {e}")
            return []

    def _async_precompute_worker(
        self, texts: List[str], progress_callback: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """"""
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

                        self.batch_processor._update_cache(miss_texts, embeddings)
                        results["new_computed"] += len(embeddings)

                    processed_count += len(batch_texts)
                    results["processed"] = processed_count

                    if progress_callback:
                        try:
                            progress_callback(processed_count, total_texts, results)
                        except Exception as e:
                            logger.warning(f"Progress callback failed: {e}")

                except Exception as e:
                    error_msg = f"Batch {i//batch_size + 1} failed: {e}"
                    results["errors"].append(error_msg)
                    logger.error(error_msg)

            logger.info(f"Precompute completed: {results}")
            return results

        except Exception as e:
            logger.error(f"Async precompute failed: {e}")
            return {"error": str(e)}

    def wait_for_background_tasks(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """
        waitingbackground taskcompleted

        Args:
            timeout: ()

        Returns:
            taskcompletedstatus
        """
        with self._task_lock:
            active_tasks = [task for task in self._background_tasks if not task.done()]

        if not active_tasks:
            return {"status": "no_active_tasks", "completed": 0, "failed": 0}

        completed = 0
        failed = 0
        cancelled = 0

        try:
            for future in as_completed(active_tasks, timeout=timeout):
                try:
                    future.result()  # getresult, ifexception
                    completed += 1
                except Exception as e:
                    logger.error(f"Background task failed: {e}")
                    failed += 1

        except TimeoutError:
            for task in active_tasks:
                if not task.done():
                    task.cancel()
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
        cancelbackground task

        Returns:
            canceltaskcount
        """
        cancelled_count = 0

        with self._task_lock:
            for task in self._background_tasks:
                if not task.done() and task.cancel():
                    cancelled_count += 1

        logger.info(f"Cancelled {cancelled_count} background tasks")
        return cancelled_count

    def _cleanup_completed_tasks(self):
        """completedtask"""
        with self._task_lock:
            self._background_tasks = [task for task in self._background_tasks if not task.done()]

            active_task_ids = {id(task) for task in self._background_tasks}
            self._task_callbacks = {
                task_id: callback for task_id, callback in self._task_callbacks.items() if task_id in active_task_ids
            }

    def get_async_status(self) -> Dict[str, Any]:
        """getstatus"""
        with self._task_lock:
            total_tasks = len(self._background_tasks)
            active_tasks = sum(1 for task in self._background_tasks if not task.done())
            completed_tasks = sum(1 for task in self._background_tasks if task.done() and not task.cancelled())
            failed_tasks = sum(
                1 for task in self._background_tasks if task.done() and not task.cancelled() and task.exception()
            )
            cancelled_tasks = sum(1 for task in self._background_tasks if task.cancelled())

        return {
            "total_tasks": total_tasks,
            "active_tasks": active_tasks,
            "completed_tasks": completed_tasks,
            "failed_tasks": failed_tasks,
            "cancelled_tasks": cancelled_tasks,
            "executor_info": {
                "max_workers": self._async_executor._max_workers,
                "thread_name_prefix": self._async_executor._thread_name_prefix,
            },
        }

    def shutdown(self, wait: bool = True):
        """close"""
        logger.info("Shutting down async embedding manager")
        self._async_executor.shutdown(wait=wait)
