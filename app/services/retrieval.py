#!/usr/bin/env python3
"""
GLM语义检索服务模块

使用GLM embeddings进行语义相似度检索，
替代原有的TF-IDF关键词匹配。
"""

from typing import List, Dict, Optional, Any
import logging

from app.repository.tasks import default_repo
from app.services.embeddings import get_embeddings_service
from app.services.config import get_config
from app.services.structure_prior import get_structure_prior_calculator
from app.services.graph_attention import get_graph_attention_reranker
from ..repository.tasks import SqliteTaskRepository

logger = logging.getLogger(__name__)


class SemanticRetrievalService:
    """GLM语义检索服务类"""
    
    def __init__(self):
        self.config = get_config()
        self.embeddings_service = get_embeddings_service()
        self.structure_calculator = get_structure_prior_calculator()
        self.graph_attention_reranker = get_graph_attention_reranker()
        self.repo = SqliteTaskRepository()
        self.min_similarity = self.config.min_similarity_threshold
        self.debug = self.config.debug_mode
        
    def search(self, query: str, k: int = 5, min_similarity: Optional[float] = None) -> List[Dict[str, Any]]:
        """
        GLM语义检索接口
        
        Args:
            query: 查询文本
            k: 返回结果数量
            min_similarity: 最小相似度阈值
            
        Returns:
            检索结果列表，每个结果包含task_id, name, content, similarity等字段
        """
        if self.debug:
            logger.debug(f"GLM语义检索: '{query}', k: {k}")
        
        try:
            # 获取查询的embedding
            query_embedding = self.embeddings_service.get_single_embedding(query)
            if not query_embedding:
                logger.warning("无法获取查询embedding")
                return []
            
            # 获取所有有embedding的任务
            tasks_with_embeddings = default_repo.get_tasks_with_embeddings()
            
            if not tasks_with_embeddings:
                logger.warning("没有找到有embedding的任务")
                return []
            
            # 准备候选项
            candidates = []
            for task in tasks_with_embeddings:
                if task.get('embedding_vector'):
                    embedding = self.embeddings_service.json_to_embedding(task['embedding_vector'])
                    if embedding:
                        candidates.append({
                            'task_id': task['id'],
                            'name': task['name'],
                            'content': task['content'] or '',
                            'embedding': embedding
                        })
            
            # 计算语义相似度
            min_sim = min_similarity if min_similarity is not None else self.min_similarity
            results = self.embeddings_service.find_most_similar(
                query_embedding, 
                candidates, 
                k=k, 
                min_similarity=min_sim
            )
            
            if self.debug:
                logger.debug(f"语义检索返回 {len(results)} 个结果")
            
            return results
        
        except Exception as e:
            logger.error(f"语义检索失败: {e}")
            return []
    
    def search_with_structure_prior(self, query: str, query_task_id: Optional[int] = None,
                                  k: int = 5, min_similarity: Optional[float] = None,
                                  structure_alpha: float = 0.3) -> List[Dict[str, Any]]:
        """Search with structure prior weights"""
        semantic_results = self.search(query, k * 2, min_similarity)
        
        if not semantic_results or not query_task_id:
            return semantic_results[:k]
        
        try:
            enhanced_results = self._apply_structure_weights(
                semantic_results, query_task_id, structure_alpha
            )
            self._log_structure_results(enhanced_results[:k], query_task_id)
            return enhanced_results[:k]
            
        except Exception as e:
            logger.warning(f"Structure prior calculation failed: {e}, falling back to semantic-only")
            return semantic_results[:k]
    
    def _apply_structure_weights(self, semantic_results: List[Dict[str, Any]], 
                               query_task_id: int, structure_alpha: float) -> List[Dict[str, Any]]:
        """Apply structure weights to semantic results"""
        candidate_ids = [result['id'] for result in semantic_results]
        
        structure_weights = self.structure_calculator.compute_structure_weights(
            query_task_id, candidate_ids
        )
        
        semantic_scores = {result['id']: result['similarity'] for result in semantic_results}
        combined_scores = self.structure_calculator.apply_structure_weights(
            semantic_scores, structure_weights, structure_alpha
        )
        
        enhanced_results = []
        for result in semantic_results:
            task_id = result['id']
            enhanced_result = result.copy()
            enhanced_result['combined_score'] = combined_scores.get(task_id, result['similarity'])
            enhanced_result['structure_weight'] = structure_weights.get(task_id, 0.0)
            enhanced_results.append(enhanced_result)
        
        enhanced_results.sort(key=lambda x: x['combined_score'], reverse=True)
        return enhanced_results
    
    def _log_structure_results(self, results: List[Dict[str, Any]], query_task_id: int) -> None:
        """Log structure-enhanced results for debugging"""
        if self.debug:
            logger.debug(f"Structure-enhanced retrieval for query_task_id={query_task_id}:")
            for i, result in enumerate(results):
                logger.debug(f"  {i+1}. Task {result['id']}: semantic={result['similarity']:.3f}, "
                           f"structure={result['structure_weight']:.3f}, combined={result['combined_score']:.3f}")
    
    def search_with_graph_attention(self, query: str, query_task_id: Optional[int] = None,
                                  k: int = 5, min_similarity: Optional[float] = None,
                                  structure_alpha: float = 0.3, attention_alpha: float = 0.4) -> List[Dict[str, Any]]:
        """Search with graph attention mechanism"""
        if not query_task_id:
            return self.search(query, k, min_similarity)
        
        structure_results = self.search_with_structure_prior(
            query, query_task_id, k * 2, min_similarity, structure_alpha
        )
        
        if len(structure_results) <= 1:
            return structure_results[:k]
        
        try:
            embeddings = self._prepare_embeddings(query, query_task_id, structure_results)
            attention_results = self.graph_attention_reranker.rerank_with_attention(
                query_task_id, structure_results, embeddings, attention_alpha
            )
            
            self._log_attention_results(attention_results[:k], query_task_id)
            return attention_results[:k]
            
        except Exception as e:
            logger.warning(f"Graph attention reranking failed: {e}, falling back to structure-only")
            return structure_results[:k]
    
    def _prepare_embeddings(self, query: str, query_task_id: int, 
                          structure_results: List[Dict[str, Any]]) -> Dict[int, List[float]]:
        """Prepare embeddings mapping for graph attention"""
        embeddings = {}
        
        query_embedding = self.embeddings_service.get_single_embedding(query)
        if query_embedding:
            embeddings[query_task_id] = query_embedding
        
        for result in structure_results:
            task_id = result['id']
            if 'embedding' in result:
                embeddings[task_id] = result['embedding']
            else:
                embeddings[task_id] = self._get_task_embedding(task_id)
        
        return embeddings
    
    def _get_task_embedding(self, task_id: int) -> Optional[List[float]]:
        """Get embedding for a specific task"""
        try:
            task_embedding = self.repo.get_task_embedding(task_id)
            if task_embedding and task_embedding.get('embedding_vector'):
                return self.embeddings_service.json_to_embedding(
                    task_embedding['embedding_vector']
                )
        except Exception:
            pass
        return None
    
    def _log_attention_results(self, results: List[Dict[str, Any]], query_task_id: int) -> None:
        """Log graph attention results for debugging"""
        if self.debug:
            logger.debug(f"Graph attention reranking for query_task_id={query_task_id}:")
            for i, result in enumerate(results):
                logger.debug(f"  {i+1}. Task {result['id']}: "
                           f"semantic={result.get('similarity', 0):.3f}, "
                           f"structure={result.get('structure_weight', 0):.3f}, "
                           f"attention={result.get('attention_score', 0):.3f}, "
                           f"final={result.get('combined_score', 0):.3f}")
    
    def get_retrieval_stats(self) -> Dict[str, Any]:
        """获取检索系统统计信息"""
        embedding_stats = self.repo.get_embedding_stats()
        
        return {
            "service": "Semantic Retrieval",
            "embeddings_service": self.embeddings_service.get_service_info(),
            "min_similarity": self.min_similarity,
            "debug_mode": self.debug,
            "structure_prior_enabled": True,
            "graph_attention_enabled": True
        }
    
    def explain_structure_weights(self, query_task_id: int, candidate_task_ids: List[int]) -> Dict[str, Any]:
        """
        解释结构先验权重的计算过程
        
        Args:
            query_task_id: 查询任务ID
            candidate_task_ids: 候选任务ID列表
            
        Returns:
            包含权重解释的详细信息
        """
        explanations = {}
        
        for candidate_id in candidate_task_ids:
            try:
                explanation = self.structure_calculator.get_structure_explanation(
                    query_task_id, candidate_id
                )
                explanations[candidate_id] = explanation
            except Exception as e:
                logger.warning(f"Failed to explain structure weight for {candidate_id}: {e}")
                explanations[candidate_id] = {'error': str(e)}
        
        return {
            'query_task_id': query_task_id,
            'explanations': explanations
        }


# 全局单例实例
_semantic_retrieval_service = None

def get_semantic_retrieval_service() -> SemanticRetrievalService:
    """获取语义检索服务单例"""
    global _semantic_retrieval_service
    if _semantic_retrieval_service is None:
        _semantic_retrieval_service = SemanticRetrievalService()
    return _semantic_retrieval_service

def clear_retrieval_cache():
    """清空检索相关缓存"""
    global _semantic_retrieval_service
    if _semantic_retrieval_service is not None:
        _semantic_retrieval_service.structure_calculator.clear_cache()
        _semantic_retrieval_service.graph_attention_reranker.clear_cache()
        logger.info("Retrieval cache cleared")