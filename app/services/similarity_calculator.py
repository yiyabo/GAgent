#!/usr/bin/env python3
"""
相似度计算器模块

专门负责向量相似度计算、最相似项查找和批量相似度比较。
从GLMEmbeddingsService中拆分出来，遵循单一职责原则。
"""

import logging
import numpy as np
from typing import List, Dict, Tuple, Any

logger = logging.getLogger(__name__)


class SimilarityCalculator:
    """相似度计算器类，专门负责向量相似度计算"""
    
    def __init__(self):
        """初始化相似度计算器"""
        logger.info("Similarity calculator initialized")
    
    def compute_similarity(self, embedding1: List[float], embedding2: List[float]) -> float:
        """
        计算两个向量的余弦相似度
        
        Args:
            embedding1: 第一个向量
            embedding2: 第二个向量
            
        Returns:
            余弦相似度值 (-1 到 1)
        """
        if not embedding1 or not embedding2:
            return 0.0
        
        if len(embedding1) != len(embedding2):
            logger.warning(f"Embedding dimensions mismatch: {len(embedding1)} vs {len(embedding2)}")
            return 0.0
        
        try:
            vec1 = np.array(embedding1, dtype=np.float32)
            vec2 = np.array(embedding2, dtype=np.float32)
            
            # 计算余弦相似度
            dot_product = np.dot(vec1, vec2)
            norm1 = np.linalg.norm(vec1)
            norm2 = np.linalg.norm(vec2)
            
            if norm1 == 0 or norm2 == 0:
                return 0.0
            
            similarity = dot_product / (norm1 * norm2)
            return float(similarity)
            
        except Exception as e:
            logger.error(f"Similarity computation failed: {e}")
            return 0.0
    
    def compute_similarities(self, query_embedding: List[float], 
                           target_embeddings: List[List[float]]) -> List[float]:
        """
        计算查询向量与多个目标向量的相似度
        
        Args:
            query_embedding: 查询向量
            target_embeddings: 目标向量列表
            
        Returns:
            相似度列表
        """
        if not query_embedding or not target_embeddings:
            return []
        
        similarities = []
        for target_embedding in target_embeddings:
            similarity = self.compute_similarity(query_embedding, target_embedding)
            similarities.append(similarity)
        
        return similarities
    
    def compute_similarities_batch(self, query_embedding: List[float], 
                                 target_embeddings: List[List[float]]) -> List[float]:
        """
        批量计算相似度（优化版本）
        
        Args:
            query_embedding: 查询向量
            target_embeddings: 目标向量列表
            
        Returns:
            相似度列表
        """
        if not query_embedding or not target_embeddings:
            return []
        
        try:
            query_vec = np.array(query_embedding, dtype=np.float32)
            target_matrix = np.array(target_embeddings, dtype=np.float32)
            
            # 批量计算余弦相似度
            dot_products = np.dot(target_matrix, query_vec)
            query_norm = np.linalg.norm(query_vec)
            target_norms = np.linalg.norm(target_matrix, axis=1)
            
            # 避免除零
            valid_mask = (target_norms != 0) & (query_norm != 0)
            similarities = np.zeros(len(target_embeddings))
            
            if query_norm != 0:
                similarities[valid_mask] = dot_products[valid_mask] / (target_norms[valid_mask] * query_norm)
            
            return similarities.tolist()
            
        except Exception as e:
            logger.error(f"Batch similarity computation failed: {e}")
            return self.compute_similarities(query_embedding, target_embeddings)
    
    def find_most_similar(self, query_embedding: List[float], 
                         candidates: List[Dict[str, Any]], 
                         k: int = 5, min_similarity: float = 0.0) -> List[Dict[str, Any]]:
        """
        查找最相似的候选项
        
        Args:
            query_embedding: 查询向量
            candidates: 候选项列表，每个包含'embedding'字段
            k: 返回前k个最相似的
            min_similarity: 最小相似度阈值
            
        Returns:
            按相似度排序的候选项列表
        """
        if not query_embedding or not candidates:
            return []
        
        # 提取embeddings
        candidate_embeddings = []
        valid_candidates = []
        
        for candidate in candidates:
            if 'embedding' in candidate and candidate['embedding']:
                candidate_embeddings.append(candidate['embedding'])
                valid_candidates.append(candidate)
        
        if not candidate_embeddings:
            logger.warning("No valid embeddings found in candidates")
            return []
        
        # 计算相似度
        similarities = self.compute_similarities_batch(query_embedding, candidate_embeddings)
        
        # 添加相似度到候选项
        for i, candidate in enumerate(valid_candidates):
            candidate['similarity'] = similarities[i]
        
        # 过滤低于阈值的候选项
        filtered_candidates = [c for c in valid_candidates if c['similarity'] >= min_similarity]
        
        # 按相似度排序
        sorted_candidates = sorted(filtered_candidates, key=lambda x: x['similarity'], reverse=True)
        
        # 返回前k个
        result = sorted_candidates[:k]
        
        logger.debug(f"Found {len(result)} most similar items from {len(candidates)} candidates")
        return result
    
    def find_similar_pairs(self, embeddings: List[List[float]], 
                          threshold: float = 0.8) -> List[Tuple[int, int, float]]:
        """
        查找相似度超过阈值的向量对
        
        Args:
            embeddings: 向量列表
            threshold: 相似度阈值
            
        Returns:
            相似向量对列表 (index1, index2, similarity)
        """
        if not embeddings or len(embeddings) < 2:
            return []
        
        similar_pairs = []
        
        try:
            embedding_matrix = np.array(embeddings, dtype=np.float32)
            
            # 计算所有向量对的相似度矩阵
            norms = np.linalg.norm(embedding_matrix, axis=1)
            normalized_embeddings = embedding_matrix / norms[:, np.newaxis]
            similarity_matrix = np.dot(normalized_embeddings, normalized_embeddings.T)
            
            # 查找超过阈值的相似对
            for i in range(len(embeddings)):
                for j in range(i + 1, len(embeddings)):
                    similarity = similarity_matrix[i, j]
                    if similarity >= threshold:
                        similar_pairs.append((i, j, float(similarity)))
            
        except Exception as e:
            logger.error(f"Similar pairs computation failed: {e}")
            # 回退到逐对计算
            for i in range(len(embeddings)):
                for j in range(i + 1, len(embeddings)):
                    similarity = self.compute_similarity(embeddings[i], embeddings[j])
                    if similarity >= threshold:
                        similar_pairs.append((i, j, similarity))
        
        # 按相似度排序
        similar_pairs.sort(key=lambda x: x[2], reverse=True)
        
        logger.debug(f"Found {len(similar_pairs)} similar pairs above threshold {threshold}")
        return similar_pairs
    
    def compute_centroid(self, embeddings: List[List[float]]) -> List[float]:
        """
        计算向量列表的质心
        
        Args:
            embeddings: 向量列表
            
        Returns:
            质心向量
        """
        if not embeddings:
            return []
        
        try:
            embedding_matrix = np.array(embeddings, dtype=np.float32)
            centroid = np.mean(embedding_matrix, axis=0)
            return centroid.tolist()
            
        except Exception as e:
            logger.error(f"Centroid computation failed: {e}")
            return []
    
    def compute_diversity_score(self, embeddings: List[List[float]]) -> float:
        """
        计算向量集合的多样性分数
        
        Args:
            embeddings: 向量列表
            
        Returns:
            多样性分数 (0-1，越高越多样)
        """
        if not embeddings or len(embeddings) < 2:
            return 0.0
        
        try:
            # 计算所有向量对的平均相似度
            total_similarity = 0.0
            pair_count = 0
            
            for i in range(len(embeddings)):
                for j in range(i + 1, len(embeddings)):
                    similarity = self.compute_similarity(embeddings[i], embeddings[j])
                    total_similarity += similarity
                    pair_count += 1
            
            if pair_count == 0:
                return 0.0
            
            avg_similarity = total_similarity / pair_count
            diversity_score = 1.0 - avg_similarity  # 相似度越低，多样性越高
            
            return max(0.0, min(1.0, diversity_score))  # 限制在0-1范围内
            
        except Exception as e:
            logger.error(f"Diversity score computation failed: {e}")
            return 0.0
