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
    
    # 核心embedding方法 - 委托给BatchProcessor
    def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        获取文本列表的向量表示（支持缓存）
        
        Args:
            texts: 文本列表
            
        Returns:
            向量列表，每个向量是float列表
        """
        return self.batch_processor.process_texts_batch(texts)
    
    def get_single_embedding(self, text: str) -> List[float]:
        """
        获取单个文本的向量表示
        
        Args:
            text: 单个文本
            
        Returns:
            向量，float列表
        """
        embeddings = self.get_embeddings([text])
        return embeddings[0] if embeddings else []
    
    # 异步方法 - 委托给AsyncEmbeddingManager
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
                         k: int = 5, min_similarity: float = 0.0) -> List[Dict[str, Any]]:
        """查找最相似的候选项"""
        return self.similarity_calculator.find_most_similar(query_embedding, candidates, k, min_similarity)
    
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
    
    def embedding_to_json(self, embedding: List[float]) -> str:
        """将embedding转换为JSON字符串用于存储"""
        import json
        return json.dumps(embedding)
    
    def json_to_embedding(self, json_str: str) -> List[float]:
        """将JSON字符串转换回embedding"""
        import json
        return json.loads(json_str)


# 单例模式获取服务实例
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
        _embeddings_service.async_manager.shutdown()
        _embeddings_service = None
