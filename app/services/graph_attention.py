#!/usr/bin/env python3
"""
图注意力机制重排模块

使用图注意力网络(GAT)对语义检索结果进行重排，
通过分析任务图的结构信息和节点特征来优化排序。
"""

import logging
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
from collections import defaultdict

from ..repository.tasks import SqliteTaskRepository

logger = logging.getLogger(__name__)


class GraphAttentionReranker:
    """图注意力重排器"""
    
    def __init__(self, repo: Optional[SqliteTaskRepository] = None):
        self.repo = repo or SqliteTaskRepository()
        
        # 注意力机制参数
        self.attention_dim = 64  # 注意力向量维度
        self.num_heads = 4       # 多头注意力数量
        self.dropout_rate = 0.1  # Dropout率
        
        # 权重参数
        self.relation_weights = {
            'requires': 1.0,
            'refers': 0.6,
            'sibling': 0.4,
            'parent': 0.7,
            'child': 0.8
        }
        
        # 缓存
        self._attention_cache = {}
    
    def rerank_with_attention(self, query_task_id: int, 
                            candidates: List[Dict[str, Any]],
                            embeddings: Dict[int, List[float]],
                            alpha: float = 0.4) -> List[Dict[str, Any]]:
        """
        使用图注意力机制重排候选结果
        
        Args:
            query_task_id: 查询任务ID
            candidates: 候选结果列表，包含similarity分数
            embeddings: 任务ID到embedding的映射
            alpha: 注意力权重的影响因子
            
        Returns:
            重排后的候选结果列表
        """
        if not candidates or len(candidates) <= 1:
            return candidates
        
        try:
            # 构建子图
            task_ids = [query_task_id] + [c['id'] for c in candidates]
            subgraph = self._build_attention_subgraph(task_ids, embeddings)
            
            # 计算注意力分数
            attention_scores = self._compute_attention_scores(
                query_task_id, subgraph
            )
            
            # 应用注意力权重重排
            reranked_candidates = self._apply_attention_reranking(
                candidates, attention_scores, alpha
            )
            
            logger.debug(f"Graph attention reranking completed for {len(candidates)} candidates")
            return reranked_candidates
            
        except Exception as e:
            logger.warning(f"Graph attention reranking failed: {e}, returning original order")
            return candidates
    
    def _build_attention_subgraph(self, task_ids: List[int], 
                                embeddings: Dict[int, List[float]]) -> Dict[str, Any]:
        """构建用于注意力计算的子图"""
        # 获取任务信息
        tasks = {}
        for task_id in task_ids:
            try:
                task_info = self.repo.get_task_info(task_id)
                if task_info:
                    tasks[task_id] = task_info
            except Exception as e:
                logger.warning(f"Failed to get task info for {task_id}: {e}")
        
        # 构建邻接矩阵和边特征
        adjacency = self._build_adjacency_matrix(task_ids, tasks)
        edge_features = self._extract_edge_features(task_ids, tasks)
        
        # 构建节点特征矩阵
        node_features = self._build_node_features(task_ids, tasks, embeddings)
        
        return {
            'task_ids': task_ids,
            'tasks': tasks,
            'adjacency': adjacency,
            'edge_features': edge_features,
            'node_features': node_features
        }
    
    def _build_adjacency_matrix(self, task_ids: List[int], 
                               tasks: Dict[int, Dict]) -> np.ndarray:
        """构建邻接矩阵"""
        n = len(task_ids)
        adjacency = np.zeros((n, n), dtype=np.float32)
        id_to_idx = {task_id: i for i, task_id in enumerate(task_ids)}
        
        # 添加依赖关系边
        for i, task_id in enumerate(task_ids):
            try:
                dependencies = self.repo.list_dependencies(task_id)
                for dep in dependencies:
                    dep_id = dep['id']
                    if dep_id in id_to_idx:
                        j = id_to_idx[dep_id]
                        kind = dep['kind']
                        weight = self.relation_weights.get(kind, 0.5)
                        adjacency[i, j] = weight
                        adjacency[j, i] = weight * 0.8  # 反向边权重稍低
            except Exception as e:
                logger.warning(f"Failed to get dependencies for task {task_id}: {e}")
        
        # 添加层次关系边
        for i, task_id in enumerate(task_ids):
            task = tasks.get(task_id, {})
            parent_id = task.get('parent_id')
            
            if parent_id and parent_id in id_to_idx:
                j = id_to_idx[parent_id]
                adjacency[i, j] = self.relation_weights['parent']
                adjacency[j, i] = self.relation_weights['child']
        
        # 添加兄弟关系边
        parent_groups = defaultdict(list)
        for task_id in task_ids:
            task = tasks.get(task_id, {})
            parent_id = task.get('parent_id')
            if parent_id:
                parent_groups[parent_id].append(task_id)
        
        for siblings in parent_groups.values():
            if len(siblings) > 1:
                for i, task_id1 in enumerate(siblings):
                    for j, task_id2 in enumerate(siblings):
                        if i != j and task_id1 in id_to_idx and task_id2 in id_to_idx:
                            idx1, idx2 = id_to_idx[task_id1], id_to_idx[task_id2]
                            adjacency[idx1, idx2] = self.relation_weights['sibling']
        
        return adjacency
    
    def _extract_edge_features(self, task_ids: List[int], 
                             tasks: Dict[int, Dict]) -> Dict[Tuple[int, int], Dict]:
        """提取边特征"""
        edge_features = {}
        id_to_idx = {task_id: i for i, task_id in enumerate(task_ids)}
        
        for i, task_id in enumerate(task_ids):
            try:
                dependencies = self.repo.list_dependencies(task_id)
                for dep in dependencies:
                    dep_id = dep['id']
                    if dep_id in id_to_idx:
                        j = id_to_idx[dep_id]
                        edge_features[(i, j)] = {
                            'relation_type': dep['kind'],
                            'weight': self.relation_weights.get(dep['kind'], 0.5),
                            'direction': 'dependency'
                        }
            except Exception:
                continue
        
        return edge_features
    
    def _build_node_features(self, task_ids: List[int], 
                           tasks: Dict[int, Dict],
                           embeddings: Dict[int, List[float]]) -> np.ndarray:
        """构建节点特征矩阵"""
        n = len(task_ids)
        
        # 获取embedding维度
        embedding_dim = 0
        for task_id in task_ids:
            if task_id in embeddings and embeddings[task_id]:
                embedding_dim = len(embeddings[task_id])
                break
        
        if embedding_dim == 0:
            embedding_dim = 1024  # 默认维度
        
        # 构建特征矩阵
        feature_dim = embedding_dim + 5  # embedding + 5个结构特征
        node_features = np.zeros((n, feature_dim), dtype=np.float32)
        
        for i, task_id in enumerate(task_ids):
            # Embedding特征
            if task_id in embeddings and embeddings[task_id]:
                embedding = embeddings[task_id]
                node_features[i, :len(embedding)] = embedding
            
            # 结构特征
            task = tasks.get(task_id, {})
            
            # 特征1: 优先级（归一化）
            priority = task.get('priority', 100)
            node_features[i, embedding_dim] = min(priority / 100.0, 1.0)
            
            # 特征2: 深度（归一化）
            depth = task.get('depth', 0)
            node_features[i, embedding_dim + 1] = min(depth / 10.0, 1.0)
            
            # 特征3: 状态编码
            status = task.get('status', 'pending')
            status_encoding = {'pending': 0.0, 'in_progress': 0.5, 'done': 1.0}
            node_features[i, embedding_dim + 2] = status_encoding.get(status, 0.0)
            
            # 特征4: 是否有父节点
            node_features[i, embedding_dim + 3] = 1.0 if task.get('parent_id') else 0.0
            
            # 特征5: 任务类型编码
            task_type = task.get('task_type', 'atomic')
            type_encoding = {'atomic': 0.0, 'composite': 1.0}
            node_features[i, embedding_dim + 4] = type_encoding.get(task_type, 0.0)
        
        return node_features
    
    def _compute_attention_scores(self, query_task_id: int, 
                                subgraph: Dict[str, Any]) -> Dict[int, float]:
        """计算图注意力分数"""
        task_ids = subgraph['task_ids']
        adjacency = subgraph['adjacency']
        node_features = subgraph['node_features']
        
        if query_task_id not in task_ids:
            return {task_id: 0.0 for task_id in task_ids}
        
        query_idx = task_ids.index(query_task_id)
        n = len(task_ids)
        
        # 简化的多头注意力机制
        attention_scores = {}
        
        for i, task_id in enumerate(task_ids):
            if i == query_idx:
                attention_scores[task_id] = 1.0  # 查询节点自身
                continue
            
            # 计算注意力权重
            attention_weight = self._compute_pairwise_attention(
                query_idx, i, node_features, adjacency
            )
            
            attention_scores[task_id] = attention_weight
        
        # 归一化注意力分数
        max_score = max(attention_scores.values()) if attention_scores else 1.0
        if max_score > 0:
            for task_id in attention_scores:
                attention_scores[task_id] /= max_score
        
        return attention_scores
    
    def _compute_pairwise_attention(self, query_idx: int, candidate_idx: int,
                                  node_features: np.ndarray, 
                                  adjacency: np.ndarray) -> float:
        """计算两个节点之间的注意力权重"""
        # 特征相似度
        query_features = node_features[query_idx]
        candidate_features = node_features[candidate_idx]
        
        # 余弦相似度
        feature_similarity = self._cosine_similarity(query_features, candidate_features)
        
        # 结构连接强度
        structural_weight = adjacency[query_idx, candidate_idx]
        
        # 组合注意力权重
        attention_weight = 0.7 * feature_similarity + 0.3 * structural_weight
        
        return max(0.0, attention_weight)
    
    def _cosine_similarity(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        """计算余弦相似度"""
        try:
            norm1 = np.linalg.norm(vec1)
            norm2 = np.linalg.norm(vec2)
            
            if norm1 == 0 or norm2 == 0:
                return 0.0
            
            similarity = np.dot(vec1, vec2) / (norm1 * norm2)
            return float(np.clip(similarity, -1.0, 1.0))
        except Exception:
            return 0.0
    
    def _apply_attention_reranking(self, candidates: List[Dict[str, Any]],
                                 attention_scores: Dict[int, float],
                                 alpha: float) -> List[Dict[str, Any]]:
        """应用注意力权重重排候选结果"""
        reranked_candidates = []
        
        for candidate in candidates:
            task_id = candidate['id']
            original_score = candidate.get('similarity', 0.0)
            attention_score = attention_scores.get(task_id, 0.0)
            
            # 组合原始分数和注意力分数
            combined_score = (1 - alpha) * original_score + alpha * attention_score
            
            reranked_candidate = candidate.copy()
            reranked_candidate['attention_score'] = attention_score
            reranked_candidate['combined_score'] = combined_score
            reranked_candidates.append(reranked_candidate)
        
        # 按组合分数排序
        reranked_candidates.sort(key=lambda x: x['combined_score'], reverse=True)
        
        return reranked_candidates
    
    def get_attention_explanation(self, query_task_id: int, 
                                candidate_task_id: int,
                                embeddings: Dict[int, List[float]]) -> Dict[str, Any]:
        """获取注意力权重的解释"""
        try:
            task_ids = [query_task_id, candidate_task_id]
            subgraph = self._build_attention_subgraph(task_ids, embeddings)
            
            query_idx = 0
            candidate_idx = 1
            
            node_features = subgraph['node_features']
            adjacency = subgraph['adjacency']
            
            # 计算各个组件的贡献
            feature_similarity = self._cosine_similarity(
                node_features[query_idx], node_features[candidate_idx]
            )
            structural_weight = adjacency[query_idx, candidate_idx]
            
            attention_weight = self._compute_pairwise_attention(
                query_idx, candidate_idx, node_features, adjacency
            )
            
            return {
                'query_task_id': query_task_id,
                'candidate_task_id': candidate_task_id,
                'feature_similarity': float(feature_similarity),
                'structural_weight': float(structural_weight),
                'attention_weight': float(attention_weight),
                'explanation': {
                    'feature_contribution': 0.7 * feature_similarity,
                    'structure_contribution': 0.3 * structural_weight
                }
            }
            
        except Exception as e:
            logger.error(f"Failed to explain attention for {query_task_id}->{candidate_task_id}: {e}")
            return {'error': str(e)}
    
    def clear_cache(self):
        """清空缓存"""
        self._attention_cache.clear()
        logger.debug("Graph attention cache cleared")


# 全局实例
_graph_attention_reranker: Optional[GraphAttentionReranker] = None


def get_graph_attention_reranker() -> GraphAttentionReranker:
    """获取图注意力重排器单例"""
    global _graph_attention_reranker
    if _graph_attention_reranker is None:
        _graph_attention_reranker = GraphAttentionReranker()
    return _graph_attention_reranker
