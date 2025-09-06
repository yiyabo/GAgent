#!/usr/bin/env python3
"""
异步Embedding管理器模块。

专门负责后台任务管理、进度回调处理、任务取消和状态跟踪。
从GLMEmbeddingsService中拆分出来，遵循单一职责原则。
"""

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class AsyncEmbeddingManager:
    """异步Embedding管理器类，专门负责异步处理"""

    def __init__(self, batch_processor):
        """
        初始化异步管理器

        Args:
            batch_processor: 批处理器实例
        """
        self.batch_processor = batch_processor

        # 异步处理相关
        self._async_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="embedding-async")
        self._background_tasks = []  # 后台任务列表
        self._task_callbacks = {}  # 任务回调映射
        self._task_lock = threading.Lock()

        logger.info("Async embedding manager initialized")

    def get_embeddings_async(self, texts: List[str], callback: Optional[Callable] = None) -> Future:
        """
        异步获取embeddings

        Args:
            texts: 文本列表
            callback: 可选的回调函数

        Returns:
            Future对象
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
        异步获取单个embedding

        Args:
            text: 单个文本
            callback: 可选的回调函数

        Returns:
            Future对象
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
        异步预计算embeddings

        Args:
            texts: 文本列表
            progress_callback: 进度回调函数

        Returns:
            Future对象
        """
        future = self._async_executor.submit(self._async_precompute_worker, texts, progress_callback)

        with self._task_lock:
            self._background_tasks.append(future)
            if progress_callback:
                self._task_callbacks[id(future)] = progress_callback

        logger.debug(f"Submitted async precompute task for {len(texts)} texts")
        return future

    def _async_embedding_worker(self, texts: List[str], callback: Optional[Callable] = None) -> List[List[float]]:
        """异步embedding工作线程"""
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
        """异步单个embedding工作线程"""
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
        """异步预计算工作线程"""
        try:
            total_texts = len(texts)
            processed_count = 0
            batch_size = self.batch_processor.get_optimal_batch_size()

            results = {"total": total_texts, "processed": 0, "cached": 0, "new_computed": 0, "errors": []}

            # 分批处理
            for i in range(0, total_texts, batch_size):
                batch_texts = texts[i : i + batch_size]

                try:
                    # 检查缓存状态
                    cached_results, cache_misses = self.batch_processor.cache.get_batch(
                        batch_texts, self.batch_processor.api_client.model
                    )

                    results["cached"] += len(batch_texts) - len(cache_misses)

                    # 处理缓存未命中的文本
                    if cache_misses:
                        miss_texts = [batch_texts[j] for j in cache_misses]
                        embeddings = self.batch_processor._compute_embeddings_batch(miss_texts)

                        # 更新缓存
                        self.batch_processor._update_cache(miss_texts, embeddings)
                        results["new_computed"] += len(embeddings)

                    processed_count += len(batch_texts)
                    results["processed"] = processed_count

                    # 调用进度回调
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
        等待后台任务完成

        Args:
            timeout: 超时时间（秒）

        Returns:
            任务完成状态
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
                    future.result()  # 获取结果，如果有异常会抛出
                    completed += 1
                except Exception as e:
                    logger.error(f"Background task failed: {e}")
                    failed += 1

        except TimeoutError:
            # 超时时取消未完成的任务
            for task in active_tasks:
                if not task.done():
                    task.cancel()
                    cancelled += 1

        # 清理已完成的任务
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
        取消所有后台任务

        Returns:
            取消的任务数量
        """
        cancelled_count = 0

        with self._task_lock:
            for task in self._background_tasks:
                if not task.done() and task.cancel():
                    cancelled_count += 1

        logger.info(f"Cancelled {cancelled_count} background tasks")
        return cancelled_count

    def _cleanup_completed_tasks(self):
        """清理已完成的任务"""
        with self._task_lock:
            # 移除已完成的任务
            self._background_tasks = [task for task in self._background_tasks if not task.done()]

            # 清理对应的回调
            active_task_ids = {id(task) for task in self._background_tasks}
            self._task_callbacks = {
                task_id: callback for task_id, callback in self._task_callbacks.items() if task_id in active_task_ids
            }

    def get_async_status(self) -> Dict[str, Any]:
        """获取异步处理状态"""
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
        """关闭异步管理器"""
        logger.info("Shutting down async embedding manager")
        self._async_executor.shutdown(wait=wait)
