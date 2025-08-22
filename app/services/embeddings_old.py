#!/usr/bin/env python3
"""
GLM Embeddings服务模块（重构版）

重构后的GLM Embeddings服务，采用组件化架构，遵循单一职责原则。
主服务类负责协调各个专门的组件，提供统一的公共接口。
"""

import logging
from typing import List, Dict, Optional, Any, Callable
from concurrent.futures import Future

from .config import get_config
from .cache import get_embedding_cache
from .glm_api_client import GLMApiClient
from .embedding_batch_processor import EmbeddingBatchProcessor
from .async_embedding_manager import AsyncEmbeddingManager
from .similarity_calculator import SimilarityCalculator

logger = logging.getLogger(__name__)


class GLMEmbeddingsService:
    """GLM Embeddings服务类（重构版）- 主要负责协调各个组件"""
    
    def __init__(self):
        """初始化服务和各个组件"""
        self.config = get_config()
        self.cache = get_embedding_cache()
        
        # 初始化各个专门的组件
        self.api_client = GLMApiClient(self.config)
        self.batch_processor = EmbeddingBatchProcessor(self.config, self.api_client, self.cache)
        self.async_manager = AsyncEmbeddingManager(self.batch_processor)
        self.similarity_calculator = SimilarityCalculator()
        
        logger.info(f"GLM Embeddings service initialized with refactored architecture")
    
    
    def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        获取文本列表的向量表示（支持缓存）
        
        Args:
            texts: 文本列表
            
        Returns:
            向量列表，每个向量是float列表
        """
        return self.batch_processor.process_texts_batch(texts)
    
    def get_embeddings_async(self, texts: List[str], callback: Optional[Callable] = None) -> Future:
        """
        异步获取embeddings
        
        Args:
            texts: 文本列表
            callback: 可选的回调函数，接收embeddings结果
            
        Returns:
            Future对象
        """
        return self.async_manager.get_embeddings_async(texts, callback)
    
    def _get_embeddings_with_callback(self, texts: List[str], 
                                     callback: Optional[Callable[[List[List[float]]], None]]) -> List[List[float]]:
        """内部方法：获取embeddings并执行回调"""
        try:
            embeddings = self.get_embeddings(texts)
            if callback:
                callback(embeddings)
            return embeddings
        except Exception as e:
            logger.error(f"Async embedding generation failed: {e}")
            if callback:
                callback([])  # 失败时返回空列表
            return []
    
    def get_single_embedding_async(self, text: str, callback: Optional[Callable] = None) -> Future:
        """
        异步获取单个embedding
        
        Args:
            text: 单个文本
            callback: 可选的回调函数
            
        Returns:
            Future对象
        """
        return self.async_manager.get_single_embedding_async(text, callback)
    
    def precompute_embeddings_async(self, texts: List[str], 
                                  progress_callback: Optional[Callable] = None) -> Future:
        """
        异步预计算embeddings
        
        Args:
            texts: 文本列表
            progress_callback: 进度回调函数
            
        Returns:
            Future对象，结果包含统计信息
        """
        return self.async_manager.precompute_embeddings_async(texts, progress_callback)
    
    def wait_for_background_tasks(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """
        等待所有后台任务完成
        
        Args:
            timeout: 超时时间（秒）
            
        Returns:
            任务完成状态
        """
        return self.async_manager.wait_for_background_tasks(timeout)
    
    def get_background_task_status(self) -> Dict[str, Any]:
        """
        获取后台任务状态
        
        Returns:
            包含任务状态信息的字典
        """
        return self.async_manager.get_async_status()
    
    def cancel_background_tasks(self) -> int:
        """
        取消所有未完成的后台任务
        
        Returns:
            成功取消的任务数量
        """
        return self.async_manager.cancel_background_tasks()
    
    # 相似度计算方法 - 委托给SimilarityCalculator
    def compute_similarity(self, embedding1: List[float], embedding2: List[float]) -> float:
        """计算两个向量的余弦相似度"""
        return self.similarity_calculator.compute_similarity(embedding1, embedding2)
    
    def compute_similarities(self, query_embedding: List[float], 
                           target_embeddings: List[List[float]]) -> List[float]:
        """计算查询向量与多个目标向量的相似度"""
        return self.similarity_calculator.compute_similarities(query_embedding, target_embeddings)
    
    def find_most_similar(self, query_embedding: List[float], 
                         candidates: List[Dict[str, Any]], 
                         top_k: int = 5) -> List[Dict[str, Any]]:
        """查找最相似的候选项"""
        return self.similarity_calculator.find_most_similar(query_embedding, candidates, top_k)
    
    # 服务信息和配置方法
    def get_service_info(self) -> Dict[str, Any]:
        """获取服务信息"""
        return {
            "service_type": "GLMEmbeddingsService",
            "version": "2.0.0-refactored",
            "config": {
                "model": self.config.embedding_model,
                "dimension": self.config.embedding_dimension,
                "mock_mode": self.config.mock_mode
            },
            "components": {
                "api_client": self.api_client.get_client_info(),
                "batch_processor": self.batch_processor.get_performance_stats(),
                "async_manager": self.async_manager.get_async_status()
            }
        }
    
    # 兼容性方法 - 保持向后兼容
    def get_optimal_batch_size(self) -> int:
        """获取最优批量大小"""
        return self.batch_processor.get_optimal_batch_size()
    
    def test_connection(self) -> bool:
        """测试API连接"""
        return self.api_client.test_connection()


# 单例模式获取服务实例
_embeddings_service = None


def get_embeddings_service() -> GLMEmbeddingsService:
    """获取GLM Embeddings服务单例"""
    global _embeddings_service
    if _embeddings_service is None:
        _embeddings_service = GLMEmbeddingsService()
    return _embeddings_service
            
            candidate_norms = np.linalg.norm(candidate_vecs, axis=1)
            # 避免除零
            candidate_norms[candidate_norms == 0] = 1.0
            
            similarities = np.dot(candidate_vecs, query_vec) / (candidate_norms * query_norm)
            
            # 确保结果在有效范围内
            similarities = np.clip(similarities, -1.0, 1.0)
            
            return similarities.tolist()
            
        except Exception as e:
            logger.error(f"Failed to compute batch similarities: {e}")
            return [0.0] * len(candidate_embeddings)
    
    def find_most_similar(self, query_embedding: List[float], 
                         candidates: List[Dict], 
                         k: int = 5, 
                         min_similarity: float = 0.0) -> List[Dict]:
        """
        找到最相似的候选项
        
        Args:
            query_embedding: 查询向量
            candidates: 候选项列表，每个候选项包含'embedding'字段
            k: 返回的最相似项数量
            min_similarity: 最小相似度阈值
            
        Returns:
            排序后的候选项列表，包含'similarity'字段
        """
        if not candidates:
            return []
        
        # 提取embeddings
        candidate_embeddings = []
        valid_candidates = []
        
        for candidate in candidates:
            embedding = candidate.get('embedding')
            if embedding and len(embedding) > 0:
                candidate_embeddings.append(embedding)
                valid_candidates.append(candidate)
        
        if not candidate_embeddings:
            return []
        
        # 计算相似度
        similarities = self.compute_similarities(query_embedding, candidate_embeddings)
        
        # 添加相似度到候选项并过滤
        results = []
        for candidate, similarity in zip(valid_candidates, similarities):
            if similarity >= min_similarity:
                candidate_copy = candidate.copy()
                candidate_copy['similarity'] = similarity
                results.append(candidate_copy)
        
        # 按相似度排序并返回top-k
        results.sort(key=lambda x: x['similarity'], reverse=True)
        return results[:k]
    
    def embedding_to_json(self, embedding: List[float]) -> str:
        """将embedding转换为JSON字符串用于存储"""
        return json.dumps(embedding)
    
    def json_to_embedding(self, json_str: str) -> List[float]:
        """从JSON字符串恢复embedding"""
        try:
            return json.loads(json_str)
        except (json.JSONDecodeError, TypeError) as e:
            logger.error(f"Failed to parse embedding JSON: {e}")
            return []
    
    def _get_optimal_batch_size(self, total_texts: int) -> int:
        """根据历史性能动态调整批量大小"""
        with self._stats_lock:
            if not self.performance_stats['batch_times']:
                return self.dynamic_batch_size
            
            # 计算最近的平均响应时间
            recent_times = self.performance_stats['batch_times'][-10:]
            recent_sizes = self.performance_stats['batch_sizes'][-10:]
            
            if len(recent_times) < 3:
                return self.dynamic_batch_size
            
            # 计算每个文本的平均处理时间
            avg_time_per_text = sum(t/s for t, s in zip(recent_times, recent_sizes)) / len(recent_times)
            
            # 目标响应时间（秒）
            target_time = 5.0
            optimal_size = int(target_time / avg_time_per_text) if avg_time_per_text > 0 else self.max_batch_size
            
            # 限制在合理范围内
            optimal_size = max(10, min(optimal_size, self.max_batch_size))
            
            # 平滑调整
            self.dynamic_batch_size = int(0.7 * self.dynamic_batch_size + 0.3 * optimal_size)
            
            logger.debug(f"Dynamic batch size adjusted to {self.dynamic_batch_size} (optimal: {optimal_size})")
            return self.dynamic_batch_size
    
    def _record_batch_performance(self, batch_size: int, timing: float) -> None:
        """记录批次性能统计"""
        with self._stats_lock:
            self.performance_stats['batch_sizes'].append(batch_size)
            self.performance_stats['batch_times'].append(timing)
            
            # 只保留最近100条记录
            if len(self.performance_stats['batch_times']) > 100:
                self.performance_stats['batch_sizes'] = self.performance_stats['batch_sizes'][-100:]
                self.performance_stats['batch_times'] = self.performance_stats['batch_times'][-100:]
    
    def get_service_info(self) -> Dict:
        """获取服务信息"""
        info = {
            "service": "GLM Embeddings",
            "model": self.model,
            "dimension": self.dimension,
            "mock_mode": self.mock_mode,
            "api_url": self.api_url,
            "max_batch_size": self.max_batch_size,
            "dynamic_batch_size": self.dynamic_batch_size,
            "max_concurrent_batches": self.max_concurrent_batches
        }
        
        # 添加缓存统计
        cache_stats = self.cache.get_stats()
        info["cache_stats"] = cache_stats
        
        # 添加性能统计
        with self._stats_lock:
            if self.performance_stats['batch_times']:
                recent_times = self.performance_stats['batch_times'][-10:]
                recent_sizes = self.performance_stats['batch_sizes'][-10:]
                info["performance_stats"] = {
                    "recent_avg_time": sum(recent_times) / len(recent_times),
                    "recent_avg_size": sum(recent_sizes) / len(recent_sizes),
                    "total_batches_processed": len(self.performance_stats['batch_times'])
                }
        
        # 添加异步任务状态
        info["async_status"] = self.get_background_task_status()
        
        return info
    
    def shutdown(self):
        """关闭服务，清理资源"""
        logger.info("Shutting down GLM Embeddings service...")
        
        # 取消所有后台任务
        cancelled = self.cancel_background_tasks()
        if cancelled > 0:
            logger.info(f"Cancelled {cancelled} background tasks")
        
        # 关闭线程池
        self._async_executor.shutdown(wait=True, timeout=30)
        
        # 关闭HTTP会话
        self.session.close()
        
        logger.info("GLM Embeddings service shutdown complete")


# 全局单例实例
_embeddings_service = None

def get_embeddings_service() -> GLMEmbeddingsService:
    """获取GLM Embeddings服务单例"""
    global _embeddings_service
    if _embeddings_service is None:
        _embeddings_service = GLMEmbeddingsService()
    return _embeddings_service


def shutdown_embeddings_service():
    """关闭embeddings服务"""
    global _embeddings_service
    if _embeddings_service is not None:
        _embeddings_service.shutdown()
        _embeddings_service = None