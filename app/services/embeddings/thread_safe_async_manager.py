#!/usr/bin/env python3
"""
线程安全的异步嵌入向量管理器模块。

解决原有异步管理器中的并发安全问题，包括：
1. 任务列表的线程安全访问
2. 回调映射的竞态条件
3. 任务状态查询的原子性
4. 资源清理的安全性
"""

import logging
import threading
import time
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class ThreadSafeAsyncManager:
    """线程安全的异步嵌入向量管理器"""

    def __init__(self, batch_processor):
        """
        初始化线程安全的异步管理器

        Args:
            batch_processor: 线程安全的批处理器实例
        """
        self.batch_processor = batch_processor

        # 异步处理相关（使用线程安全容器）
        self._async_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="embedding-async-safe")

        # 使用锁保护的任务管理
        self._tasks_lock = threading.RLock()
        self._background_tasks = []  # 后台任务列表
        self._task_callbacks = {}  # 任务ID到回调的映射
        self._task_metadata = {}  # 任务元数据

        # 任务统计（线程安全）
        self._stats_lock = threading.RLock()
        self._task_counters = defaultdict(int)  # 各类任务计数器
        self._completion_times = []  # 完成时间记录

        # 定期清理线程
        self._cleanup_thread = None
        self._cleanup_interval = 300  # 5分钟清理一次
        self._shutdown_event = threading.Event()
        self._start_cleanup_thread()

        logger.info("Thread-safe async embedding manager initialized")

    def _start_cleanup_thread(self):
        """启动定期清理线程"""

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
        线程安全异步获取嵌入向量

        Args:
            texts: 文本列表
            callback: 可选的回调函数

        Returns:
            Future对象
        """
        if not texts:
            # 创建一个已完成的Future
            future = Future()
            future.set_result([])
            return future

        # 创建任务
        future = self._async_executor.submit(self._async_embedding_worker, texts, callback, task_type="get_embeddings")

        # 线程安全地注册任务
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

        # 更新统计
        with self._stats_lock:
            self._task_counters["get_embeddings"] += 1

        logger.debug(f"Submitted async embedding task for {len(texts)} texts")
        return future

    def get_single_embedding_async(self, text: str, callback: Optional[Callable] = None) -> Future:
        """
        线程安全异步获取单个嵌入向量

        Args:
            text: 单个文本
            callback: 可选的回调函数

        Returns:
            Future对象
        """
        if not text.strip():
            # 创建一个已完成的Future
            future = Future()
            future.set_result([])
            return future

        # 创建任务
        future = self._async_executor.submit(
            self._async_single_embedding_worker, text, callback, task_type="get_single_embedding"
        )

        # 线程安全地注册任务
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

        # 更新统计
        with self._stats_lock:
            self._task_counters["get_single_embedding"] += 1

        logger.debug("Submitted async single embedding task")
        return future

    def precompute_embeddings_async(self, texts: List[str], progress_callback: Optional[Callable] = None) -> Future:
        """
        线程安全异步预计算嵌入向量

        Args:
            texts: 文本列表
            progress_callback: 进度回调函数

        Returns:
            Future对象
        """
        if not texts:
            # 创建一个已完成的Future
            future = Future()
            future.set_result({"total": 0, "processed": 0, "cached": 0, "new_computed": 0, "errors": []})
            return future

        # 创建任务
        future = self._async_executor.submit(
            self._async_precompute_worker, texts, progress_callback, task_type="precompute_embeddings"
        )

        # 线程安全地注册任务
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

        # 更新统计
        with self._stats_lock:
            self._task_counters["precompute_embeddings"] += 1

        logger.debug(f"Submitted async precompute task for {len(texts)} texts")
        return future

    def _async_embedding_worker(
        self, texts: List[str], callback: Optional[Callable] = None, task_type: str = None
    ) -> List[List[float]]:
        """线程安全的异步嵌入向量工作线程"""
        start_time = time.time()
        try:
            embeddings = self.batch_processor.process_texts_batch(texts)

            # 安全执行回调
            if callback:
                self._safe_execute_callback(callback, embeddings)

            # 记录完成时间
            completion_time = time.time() - start_time
            with self._stats_lock:
                self._completion_times.append(completion_time)
                if len(self._completion_times) > 100:  # 限制历史记录
                    self._completion_times.pop(0)

            return embeddings

        except Exception as e:
            logger.error(f"Async embedding generation failed: {e}")

            # 错误回调
            if callback:
                self._safe_execute_callback(callback, [], error=e)

            return []

    def _async_single_embedding_worker(
        self, text: str, callback: Optional[Callable] = None, task_type: str = None
    ) -> List[float]:
        """线程安全的异步单个嵌入向量工作线程"""
        start_time = time.time()
        try:
            embeddings = self.batch_processor.process_texts_batch([text])
            result = embeddings[0] if embeddings else []

            # 安全执行回调
            if callback:
                self._safe_execute_callback(callback, result)

            # 记录完成时间
            completion_time = time.time() - start_time
            with self._stats_lock:
                self._completion_times.append(completion_time)
                if len(self._completion_times) > 100:
                    self._completion_times.pop(0)

            return result

        except Exception as e:
            logger.error(f"Async single embedding generation failed: {e}")

            # 错误回调
            if callback:
                self._safe_execute_callback(callback, [], error=e)

            return []

    def _async_precompute_worker(
        self, texts: List[str], progress_callback: Optional[Callable] = None, task_type: str = None
    ) -> Dict[str, Any]:
        """线程安全的异步预计算工作线程"""
        start_time = time.time()
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
                        self.batch_processor._update_cache_atomic(miss_texts, embeddings)
                        results["new_computed"] += len(embeddings)

                    processed_count += len(batch_texts)
                    results["processed"] = processed_count

                    # 安全调用进度回调
                    if progress_callback:
                        self._safe_execute_callback(progress_callback, processed_count, total_texts, results)

                except Exception as e:
                    error_msg = f"Batch {i//batch_size + 1} failed: {e}"
                    results["errors"].append(error_msg)
                    logger.error(error_msg)

            # 记录完成时间
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
        """安全执行回调函数，捕获异常"""
        try:
            if error:
                # 如果回调支持错误参数
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
        线程安全等待后台任务完成

        Args:
            timeout: 超时时间（秒）

        Returns:
            任务完成状态
        """
        # 获取活动任务的快照
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
                    future.result()  # 获取结果，如果有异常会抛出
                    completed += 1
                except Exception as e:
                    logger.error(f"Background task failed: {e}")
                    failed += 1

        except TimeoutError:
            # 超时时取消未完成的任务
            for task in active_tasks:
                if not task.done():
                    if task.cancel():
                        cancelled += 1

        # 触发清理
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
        线程安全取消所有后台任务

        Returns:
            取消的任务数量
        """
        cancelled_count = 0

        with self._tasks_lock:
            for task in self._background_tasks:
                if not task.done() and task.cancel():
                    cancelled_count += 1

        logger.info(f"Cancelled {cancelled_count} background tasks")
        return cancelled_count

    def _cleanup_completed_tasks(self):
        """清理已完成的任务（线程安全）"""
        with self._tasks_lock:
            # 移除已完成的任务
            before_count = len(self._background_tasks)
            self._background_tasks = [task for task in self._background_tasks if not task.done()]
            after_count = len(self._background_tasks)

            # 清理对应的元数据和回调
            active_task_ids = {id(task) for task in self._background_tasks}

            # 清理元数据
            removed_metadata = [task_id for task_id in self._task_metadata.keys() if task_id not in active_task_ids]
            for task_id in removed_metadata:
                del self._task_metadata[task_id]

            # 清理回调
            removed_callbacks = [task_id for task_id in self._task_callbacks.keys() if task_id not in active_task_ids]
            for task_id in removed_callbacks:
                del self._task_callbacks[task_id]

        cleaned_count = before_count - after_count
        if cleaned_count > 0:
            logger.debug(f"Cleaned up {cleaned_count} completed tasks")

    def get_async_status(self) -> Dict[str, Any]:
        """获取线程安全的异步处理状态"""
        with self._tasks_lock:
            total_tasks = len(self._background_tasks)
            active_tasks = sum(1 for task in self._background_tasks if not task.done())
            completed_tasks = sum(1 for task in self._background_tasks if task.done() and not task.cancelled())
            failed_tasks = sum(
                1 for task in self._background_tasks if task.done() and not task.cancelled() and task.exception()
            )
            cancelled_tasks = sum(1 for task in self._background_tasks if task.cancelled())

            # 任务类型分布
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
        """关闭异步管理器（线程安全）"""
        logger.info("Shutting down thread-safe async embedding manager")

        # 设置关闭标志
        self._shutdown_event.set()

        # 等待清理线程结束
        if self._cleanup_thread and self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=5.0)

        # 关闭执行器
        self._async_executor.shutdown(wait=wait)

        # 清理资源
        with self._tasks_lock:
            self._background_tasks.clear()
            self._task_callbacks.clear()
            self._task_metadata.clear()
