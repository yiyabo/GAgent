#!/usr/bin/env python3
"""
线程安全的批处理器模块。

解决原有批处理器中的并发安全问题，包括：
1. 批量大小动态调整的竞态条件
2. 缓存更新操作的原子性
3. API调用去重的线程安全性
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ThreadSafeBatchProcessor:
    """线程安全的嵌入向量批处理器"""

    def __init__(self, config, api_client, cache):
        """
        初始化线程安全的批处理器

        Args:
            config: 配置对象
            api_client: API客户端
            cache: 线程安全缓存实例
        """
        self.config = config
        self.api_client = api_client
        self.cache = cache

        # 性能统计相关（线程安全）
        self._stats_lock = threading.RLock()
        self._batch_count = 0
        self._total_texts = 0
        self._cache_hits = 0
        self._api_calls = 0
        self._total_time = 0.0

        # 动态批大小控制（线程安全）
        self._batch_size_lock = threading.RLock()
        self._current_batch_size = getattr(config, "embedding_batch_size", 10)
        self._performance_history = []
        self._last_adjustment_time = 0

        # API调用去重（避免同时请求相同内容）
        self._active_requests = {}  # text_hash -> Future
        self._request_lock = threading.RLock()

        # 线程池用于并发处理
        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="batch-processor")

        logger.info("Thread-safe batch processor initialized")

    def process_texts_batch(self, texts: List[str]) -> List[List[float]]:
        """
        线程安全处理文本批次

        Args:
            texts: 文本列表

        Returns:
            嵌入向量列表
        """
        if not texts:
            return []

        start_time = time.time()

        try:
            # 1. 批量检查缓存
            cached_results, cache_misses = self.cache.get_batch(texts, self.api_client.model)

            with self._stats_lock:
                self._cache_hits += len(texts) - len(cache_misses)

            # 2. 处理缓存未命中的文本
            if cache_misses:
                miss_texts = [texts[i] for i in cache_misses]
                new_embeddings = self._compute_embeddings_with_deduplication(miss_texts)

                # 3. 原子更新缓存
                if new_embeddings:
                    self._update_cache_atomic(miss_texts, new_embeddings)

                # 4. 合并结果
                for i, miss_idx in enumerate(cache_misses):
                    if i < len(new_embeddings):
                        cached_results[miss_idx] = new_embeddings[i]

            # 5. 更新性能统计
            processing_time = time.time() - start_time
            self._update_performance_stats(len(texts), len(cache_misses), processing_time)

            # 确保返回完整结果
            return [result for result in cached_results if result is not None]

        except Exception as e:
            logger.error(f"Batch processing failed: {e}")
            return [[] for _ in texts]

    def _compute_embeddings_with_deduplication(self, texts: List[str]) -> List[List[float]]:
        """
        带去重功能的嵌入向量计算

        Args:
            texts: 文本列表

        Returns:
            嵌入向量列表
        """
        # 1. 按文本内容去重，记录索引映射
        unique_texts = []
        text_to_indices = {}

        for i, text in enumerate(texts):
            text_hash = self._compute_text_hash(text)
            if text_hash not in text_to_indices:
                text_to_indices[text_hash] = []
                unique_texts.append(text)
            text_to_indices[text_hash].append(i)

        # 2. 检查是否有正在进行的请求
        pending_requests = []
        texts_to_compute = []

        with self._request_lock:
            for text in unique_texts:
                text_hash = self._compute_text_hash(text)
                if text_hash in self._active_requests:
                    # 等待现有请求
                    pending_requests.append((text, self._active_requests[text_hash]))
                else:
                    # 需要新建请求
                    texts_to_compute.append(text)

        # 3. 等待现有请求完成
        embeddings_map = {}
        for text, future in pending_requests:
            try:
                embeddings_map[text] = future.result(timeout=30)
            except Exception as e:
                logger.warning(f"Waiting for existing request failed: {e}")
                texts_to_compute.append(text)

        # 4. 计算新的嵌入向量
        if texts_to_compute:
            new_embeddings = self._compute_embeddings_batch(texts_to_compute)
            for i, text in enumerate(texts_to_compute):
                if i < len(new_embeddings):
                    embeddings_map[text] = new_embeddings[i]

        # 5. 重新构建完整结果
        results = []
        for text in texts:
            if text in embeddings_map:
                results.append(embeddings_map[text])
            else:
                results.append([])

        return results

    def _compute_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        """
        计算嵌入向量批次（带去重请求管理）

        Args:
            texts: 文本列表

        Returns:
            嵌入向量列表
        """
        if not texts:
            return []

        # 注册活动请求
        futures_map = {}
        with self._request_lock:
            for text in texts:
                text_hash = self._compute_text_hash(text)
                if text_hash not in self._active_requests:
                    future = self._executor.submit(self._single_api_call, [text])
                    self._active_requests[text_hash] = future
                    futures_map[text] = future

        try:
            # 获取当前最优批大小
            batch_size = self._get_optimal_batch_size()

            # 分批调用API
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
                    # 添加空结果以保持索引对应
                    results.extend([[] for _ in batch_texts])

            return results

        finally:
            # 清理活动请求
            with self._request_lock:
                for text in texts:
                    text_hash = self._compute_text_hash(text)
                    self._active_requests.pop(text_hash, None)

    def _single_api_call(self, texts: List[str]) -> List[List[float]]:
        """单次API调用（用于去重请求）"""
        return self.api_client.get_embeddings(texts)

    def _update_cache_atomic(self, texts: List[str], embeddings: List[List[float]]) -> None:
        """原子更新缓存"""
        if len(texts) != len(embeddings):
            logger.warning(f"Texts and embeddings length mismatch: {len(texts)} vs {len(embeddings)}")
            return

        try:
            self.cache.put_batch(texts, embeddings, self.api_client.model)
        except Exception as e:
            logger.error(f"Failed to update cache: {e}")

    def _update_performance_stats(self, total_texts: int, cache_misses: int, processing_time: float):
        """更新性能统计（线程安全）"""
        with self._stats_lock:
            self._batch_count += 1
            self._total_texts += total_texts
            self._total_time += processing_time

            # 记录性能历史用于动态调整
            if cache_misses > 0:  # 只有当有实际API调用时才记录
                throughput = cache_misses / processing_time if processing_time > 0 else 0
                with self._batch_size_lock:
                    self._performance_history.append(
                        {"batch_size": self._current_batch_size, "throughput": throughput, "timestamp": time.time()}
                    )

                    # 限制历史记录长度
                    if len(self._performance_history) > 10:
                        self._performance_history.pop(0)

                    # 定期调整批大小
                    self._maybe_adjust_batch_size()

    def _maybe_adjust_batch_size(self):
        """可能调整批大小（在锁保护下）"""
        current_time = time.time()

        # 每60秒最多调整一次
        if current_time - self._last_adjustment_time < 60:
            return

        if len(self._performance_history) < 3:
            return

        # 分析性能趋势
        recent_throughputs = [record["throughput"] for record in self._performance_history[-3:]]
        avg_throughput = sum(recent_throughputs) / len(recent_throughputs)

        # 简单的自适应策略
        if avg_throughput > 0:
            if all(t >= avg_throughput * 0.9 for t in recent_throughputs):
                # 性能稳定，可能可以增加批大小
                if self._current_batch_size < 50:
                    self._current_batch_size = min(self._current_batch_size + 5, 50)
                    logger.info(f"Increased batch size to {self._current_batch_size}")
            elif avg_throughput < recent_throughputs[0] * 0.8:
                # 性能下降，减少批大小
                if self._current_batch_size > 5:
                    self._current_batch_size = max(self._current_batch_size - 2, 5)
                    logger.info(f"Decreased batch size to {self._current_batch_size}")

        self._last_adjustment_time = current_time

    def _get_optimal_batch_size(self) -> int:
        """获取当前最优批大小（线程安全）"""
        with self._batch_size_lock:
            return self._current_batch_size

    def get_optimal_batch_size(self) -> int:
        """公共接口：获取最优批大小"""
        return self._get_optimal_batch_size()

    def _compute_text_hash(self, text: str) -> str:
        """计算文本哈希"""
        import hashlib

        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def get_performance_stats(self) -> Dict[str, Any]:
        """获取性能统计（线程安全）"""
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
        """为已完成任务预计算嵌入向量"""
        try:
            from ..database import init_db
            from ..repository.tasks import SqliteTaskRepository

            init_db()
            repo = SqliteTaskRepository()

            # 获取已完成任务
            completed_tasks = repo.list_all_tasks()
            completed_tasks = [t for t in completed_tasks if t.get("status") == "completed"]

            if not completed_tasks:
                return 0

            # 提取任务内容
            texts = []
            for task in completed_tasks:
                content = task.get("content", "")
                if content and content.strip():
                    texts.append(content.strip())

            if not texts:
                return 0

            # 批量预计算
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
        """关闭批处理器"""
        logger.info("Shutting down thread-safe batch processor")
        self._executor.shutdown(wait=True)
