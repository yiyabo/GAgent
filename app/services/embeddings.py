#!/usr/bin/env python3
"""
GLM Embeddings服务模块

提供基于智谱GLM的embedding生成和语义相似度计算功能，
用于替代传统的TF-IDF检索，实现更精准的语义检索。
"""

import os
import json
import time
import requests
import numpy as np
from typing import List, Dict, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class GLMEmbeddingsService:
    """GLM Embeddings服务类"""
    
    def __init__(self):
        self.api_key = os.getenv('GLM_API_KEY')
        self.api_url = os.getenv('GLM_API_URL', 'https://open.bigmodel.cn/api/paas/v4/embeddings')
        self.model = "embedding-2"  # GLM的embedding模型
        self.dimension = 1024  # GLM embedding维度
        self.max_batch_size = 25  # GLM API批量处理上限
        self.max_retries = 3
        self.retry_delay = 1.0
        
        # Mock模式检查
        self.mock_mode = self._parse_bool(os.getenv('LLM_MOCK', '0'))
        
        if not self.mock_mode and not self.api_key:
            logger.warning("GLM_API_KEY未设置，将使用Mock模式")
            self.mock_mode = True
    
    def _parse_bool(self, val) -> bool:
        """解析布尔值"""
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() in ('true', '1', 'yes', 'on')
        return bool(val)
    
    def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        获取文本列表的向量表示
        
        Args:
            texts: 文本列表
            
        Returns:
            向量列表，每个向量是float列表
        """
        if not texts:
            return []
            
        if self.mock_mode:
            return self._get_mock_embeddings(texts)
        
        # 分批处理大量文本
        all_embeddings = []
        for i in range(0, len(texts), self.max_batch_size):
            batch = texts[i:i + self.max_batch_size]
            batch_embeddings = self._get_embeddings_batch(batch)
            all_embeddings.extend(batch_embeddings)
        
        return all_embeddings
    
    def _get_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        """获取单批文本的embeddings"""
        for attempt in range(self.max_retries):
            try:
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                }
                
                payload = {
                    "model": self.model,
                    "input": texts
                }
                
                logger.debug(f"请求GLM Embeddings API，文本数量: {len(texts)}")
                
                response = requests.post(
                    self.api_url, 
                    json=payload, 
                    headers=headers,
                    timeout=30
                )
                
                response.raise_for_status()
                data = response.json()
                
                if 'data' not in data:
                    raise ValueError(f"API响应格式错误: {data}")
                
                embeddings = [item['embedding'] for item in data['data']]
                
                logger.debug(f"成功获取 {len(embeddings)} 个embeddings")
                return embeddings
                
            except Exception as e:
                logger.warning(f"获取embeddings失败 (尝试 {attempt + 1}/{self.max_retries}): {e}")
                
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))
                else:
                    logger.error(f"获取embeddings最终失败，返回Mock数据")
                    return self._get_mock_embeddings(texts)
        
        return []
    
    def _get_mock_embeddings(self, texts: List[str]) -> List[List[float]]:
        """生成Mock embeddings用于测试"""
        np.random.seed(42)  # 确保可重复性
        
        embeddings = []
        for text in texts:
            # 基于文本内容生成确定性的向量
            text_hash = hash(text) % (2**32)
            np.random.seed(text_hash)
            
            # 生成标准化的随机向量
            vector = np.random.normal(0, 1, self.dimension)
            vector = vector / np.linalg.norm(vector)  # 单位向量
            
            embeddings.append(vector.tolist())
        
        logger.debug(f"生成 {len(embeddings)} 个Mock embeddings")
        return embeddings
    
    def get_single_embedding(self, text: str) -> List[float]:
        """获取单个文本的embedding"""
        embeddings = self.get_embeddings([text])
        return embeddings[0] if embeddings else []
    
    def compute_similarity(self, embedding1: List[float], embedding2: List[float]) -> float:
        """
        计算两个向量的余弦相似度
        
        Args:
            embedding1: 第一个向量
            embedding2: 第二个向量
            
        Returns:
            余弦相似度值 (-1 到 1)
        """
        try:
            vec1 = np.array(embedding1, dtype=np.float32)
            vec2 = np.array(embedding2, dtype=np.float32)
            
            # 处理零向量情况
            norm1 = np.linalg.norm(vec1)
            norm2 = np.linalg.norm(vec2)
            
            if norm1 == 0 or norm2 == 0:
                return 0.0
            
            similarity = np.dot(vec1, vec2) / (norm1 * norm2)
            
            # 确保结果在有效范围内
            return float(np.clip(similarity, -1.0, 1.0))
            
        except Exception as e:
            logger.error(f"计算相似度失败: {e}")
            return 0.0
    
    def compute_similarities(self, query_embedding: List[float], 
                           candidate_embeddings: List[List[float]]) -> List[float]:
        """
        批量计算查询向量与候选向量的相似度
        
        Args:
            query_embedding: 查询向量
            candidate_embeddings: 候选向量列表
            
        Returns:
            相似度列表
        """
        try:
            query_vec = np.array(query_embedding, dtype=np.float32)
            candidate_vecs = np.array(candidate_embeddings, dtype=np.float32)
            
            # 批量计算余弦相似度
            query_norm = np.linalg.norm(query_vec)
            if query_norm == 0:
                return [0.0] * len(candidate_embeddings)
            
            candidate_norms = np.linalg.norm(candidate_vecs, axis=1)
            # 避免除零
            candidate_norms[candidate_norms == 0] = 1.0
            
            similarities = np.dot(candidate_vecs, query_vec) / (candidate_norms * query_norm)
            
            # 确保结果在有效范围内
            similarities = np.clip(similarities, -1.0, 1.0)
            
            return similarities.tolist()
            
        except Exception as e:
            logger.error(f"批量计算相似度失败: {e}")
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
            logger.error(f"解析embedding JSON失败: {e}")
            return []
    
    def get_service_info(self) -> Dict:
        """获取服务信息"""
        return {
            "service": "GLM Embeddings",
            "model": self.model,
            "dimension": self.dimension,
            "mock_mode": self.mock_mode,
            "api_url": self.api_url,
            "max_batch_size": self.max_batch_size
        }


# 全局单例实例
_embeddings_service = None

def get_embeddings_service() -> GLMEmbeddingsService:
    """获取GLM Embeddings服务单例"""
    global _embeddings_service
    if _embeddings_service is None:
        _embeddings_service = GLMEmbeddingsService()
    return _embeddings_service