#!/usr/bin/env python3
"""
线程安全的嵌入向量服务模块

解决原有服务中的并发安全问题，包括：
1. 单例模式的竞态条件
2. 缓存操作的线程安全性
3. 异步任务管理的并发控制
"""

import logging
import threading
from concurrent.futures import Future
from typing import Any, Callable, Dict, List, Optional

from app.services.foundation.config import get_config
from app.services.embeddings.glm_api_client import GLMApiClient
from app.services.embeddings.similarity_calculator import SimilarityCalculator
from app.services.embeddings.thread_safe_async_manager import ThreadSafeAsyncManager
from app.services.embeddings.thread_safe_batch_processor import ThreadSafeBatchProcessor
from app.services.embeddings.thread_safe_cache import get_thread_safe_embedding_cache

logger = logging.getLogger(__name__)


class ThreadSafeEmbeddingsService:
    """线程安全的GLM嵌入向量服务类"""

    def __init__(self):
        """初始化线程安全的服务组件"""
        self.config = get_config()
        self.cache = get_thread_safe_embedding_cache()

        # 初始化线程安全的专用组件
        self.api_client = GLMApiClient(self.config)
        self.batch_processor = ThreadSafeBatchProcessor(self.config, self.api_client, self.cache)
        self.async_manager = ThreadSafeAsyncManager(self.batch_processor)
        self.similarity_calculator = SimilarityCalculator()

        # 服务级别的锁
        self._service_lock = threading.RLock()

        logger.info("Thread-safe GLM embeddings service initialized")

    # 核心嵌入方法 - 委托给线程安全批处理器
    def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        线程安全获取文本列表的向量表示（带缓存支持）

        Args:
            texts: 文本列表

        Returns:
            向量列表，每个向量是一个浮点数列表
        """
        with self._service_lock:
            return self.batch_processor.process_texts_batch(texts)

    def get_single_embedding(self, text: str) -> List[float]:
        """
        线程安全获取单个文本的向量表示

        Args:
            text: 单个文本

        Returns:
            向量作为浮点数列表
        """
        embeddings = self.get_embeddings([text])
        return embeddings[0] if embeddings else []

    # 异步方法 - 委托给线程安全异步管理器
    def get_embeddings_async(self, texts: List[str], callback: Optional[Callable] = None) -> Future:
        """
        异步获取嵌入向量（线程安全）

        Args:
            texts: 文本列表
            callback: 可选的回调函数接收嵌入向量结果

        Returns:
            Future对象
        """
        return self.async_manager.get_embeddings_async(texts, callback)

    def get_single_embedding_async(self, text: str, callback: Optional[Callable] = None) -> Future:
        """
        异步获取单个嵌入向量（线程安全）

        Args:
            text: 单个文本
            callback: 可选的回调函数

        Returns:
            Future对象
        """
        return self.async_manager.get_single_embedding_async(text, callback)

    def precompute_embeddings_async(self, texts: List[str], progress_callback: Optional[Callable] = None) -> Future:
        """
        异步预计算嵌入向量（线程安全）

        Args:
            texts: 文本列表
            progress_callback: 进度回调函数

        Returns:
            Future对象，结果包含统计信息
        """
        return self.async_manager.precompute_embeddings_async(texts, progress_callback)

    def wait_for_background_tasks(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """
        等待所有后台任务完成（线程安全）

        Args:
            timeout: 超时时间（秒）

        Returns:
            任务完成状态
        """
        return self.async_manager.wait_for_background_tasks(timeout)

    def get_background_task_status(self) -> Dict[str, Any]:
        """
        获取后台任务状态（线程安全）

        Returns:
            包含任务状态信息的字典
        """
        return self.async_manager.get_async_status()

    def cancel_background_tasks(self) -> int:
        """
        取消所有未完成的后台任务（线程安全）

        Returns:
            成功取消的任务数量
        """
        return self.async_manager.cancel_background_tasks()

    # 相似度计算方法 - 委托给相似度计算器
    def compute_similarity(self, embedding1: List[float], embedding2: List[float]) -> float:
        """计算两个向量之间的余弦相似度"""
        return self.similarity_calculator.compute_similarity(embedding1, embedding2)

    def compute_similarities(self, query_embedding: List[float], target_embeddings: List[List[float]]) -> List[float]:
        """计算查询向量与多个目标向量之间的相似度"""
        return self.similarity_calculator.compute_similarities(query_embedding, target_embeddings)

    def find_most_similar(
        self, query_embedding: List[float], candidates: List[Dict[str, Any]], k: int = 5, min_similarity: float = 0.0
    ) -> List[Dict[str, Any]]:
        """查找最相似的候选项"""
        return self.similarity_calculator.find_most_similar(query_embedding, candidates, k, min_similarity)

    # 服务信息和配置方法
    def get_service_info(self) -> Dict[str, Any]:
        """获取线程安全的服务信息"""
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

    # 兼容性方法 - 保持向后兼容性
    def get_optimal_batch_size(self) -> int:
        """获取最优批处理大小"""
        return self.batch_processor.get_optimal_batch_size()

    def test_connection(self) -> bool:
        """测试API连接"""
        return self.api_client.test_connection()

    def embedding_to_json(self, embedding: List[float]) -> str:
        """将嵌入向量转换为JSON字符串用于存储"""
        import json

        return json.dumps(embedding)

    def json_to_embedding(self, json_str: str) -> List[float]:
        """将JSON字符串转换回嵌入向量"""
        import json

        return json.loads(json_str)

    def precompute_embeddings_for_completed_tasks(self, batch_size: int = 10) -> int:
        """为已完成的任务预计算嵌入向量（线程安全）"""
        return self.batch_processor.precompute_for_completed_tasks(batch_size)

    def shutdown(self) -> None:
        """关闭服务并清理资源"""
        with self._service_lock:
            logger.info("Shutting down thread-safe embeddings service")
            self.async_manager.shutdown()
            self.cache.shutdown()


# 线程安全的单例模式实现
_thread_safe_service: Optional[ThreadSafeEmbeddingsService] = None
_service_creation_lock = threading.Lock()


def get_thread_safe_embeddings_service() -> ThreadSafeEmbeddingsService:
    """获取线程安全的GLM嵌入向量服务单例"""
    global _thread_safe_service

    if _thread_safe_service is None:
        with _service_creation_lock:
            # 双重检查锁定模式，确保线程安全的单例创建
            if _thread_safe_service is None:
                _thread_safe_service = ThreadSafeEmbeddingsService()

    return _thread_safe_service


def shutdown_thread_safe_embeddings_service():
    """关闭线程安全的嵌入向量服务"""
    global _thread_safe_service

    with _service_creation_lock:
        if _thread_safe_service is not None:
            _thread_safe_service.shutdown()
            _thread_safe_service = None


# 为了兼容现有代码，提供别名
def get_embeddings_service() -> ThreadSafeEmbeddingsService:
    """获取嵌入向量服务（线程安全版本）"""
    return get_thread_safe_embeddings_service()


def shutdown_embeddings_service():
    """关闭嵌入向量服务（线程安全版本）"""
    shutdown_thread_safe_embeddings_service()
