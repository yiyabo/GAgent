#!/usr/bin/env python3
"""
混合检索服务模块

结合传统TF-IDF关键词匹配和GLM语义向量检索，
提供更准确和全面的任务上下文检索功能。
"""

import os
import json
import logging
from typing import List, Dict, Optional, Any, Tuple
from collections import Counter
import re
import math

from .embeddings import get_embeddings_service
from ..repository.tasks import default_repo

logger = logging.getLogger(__name__)


class HybridRetrievalService:
    """混合检索服务类"""
    
    def __init__(self):
        self.embeddings_service = get_embeddings_service()
        
        # 检索配置
        self.keyword_weight = float(os.getenv('RETRIEVAL_KEYWORD_WEIGHT', '0.3'))
        self.semantic_weight = float(os.getenv('RETRIEVAL_SEMANTIC_WEIGHT', '0.7'))
        self.min_keyword_score = float(os.getenv('RETRIEVAL_MIN_KEYWORD_SCORE', '0.1'))
        self.min_semantic_score = float(os.getenv('RETRIEVAL_MIN_SEMANTIC_SCORE', '0.2'))
        
        # TF-IDF参数
        self.max_candidates = int(os.getenv('TFIDF_MAX_CANDIDATES', '500'))
        self.min_tfidf_score = float(os.getenv('TFIDF_MIN_SCORE', '0.0'))
        
        # 启用调试日志
        self.debug = os.getenv('RETRIEVAL_DEBUG', '0') == '1'
        
    def search(self, query: str, k: int = 5, method: str = "hybrid", 
               min_score: Optional[float] = None, max_candidates: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        统一检索接口
        
        Args:
            query: 查询文本
            k: 返回结果数量
            method: 检索方法 ("tfidf", "semantic", "hybrid")
            
        Returns:
            检索结果列表
        """
        if self.debug:
            logger.debug(f"检索查询: '{query}', 方法: {method}, k: {k}")
        
        if method == "tfidf":
            return self.tfidf_search(query, k, min_score=min_score, max_candidates=max_candidates)
        elif method == "semantic":
            return self.semantic_search(query, k, min_score=min_score)
        elif method == "hybrid":
            return self.hybrid_search(query, k, min_score=min_score, max_candidates=max_candidates)
        else:
            logger.warning(f"未知检索方法: {method}, 使用hybrid")
            return self.hybrid_search(query, k, min_score=min_score, max_candidates=max_candidates)
    
    def semantic_search(self, query: str, k: int = 5, min_score: Optional[float] = None) -> List[Dict[str, Any]]:
        """
        纯语义检索：基于GLM embeddings
        """
        try:
            # 获取查询的embedding
            query_embedding = self.embeddings_service.get_single_embedding(query)
            if not query_embedding:
                logger.warning("无法获取查询embedding，回退到TF-IDF")
                return self.tfidf_search(query, k)
            
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
            min_similarity = min_score if min_score is not None else self.min_semantic_score
            results = self.embeddings_service.find_most_similar(
                query_embedding, 
                candidates, 
                k=k, 
                min_similarity=min_similarity
            )
            
            if self.debug:
                logger.debug(f"语义检索返回 {len(results)} 个结果")
            
            return results
            
        except Exception as e:
            logger.error(f"语义检索失败: {e}")
            return self.tfidf_search(query, k, min_score=min_score)  # 回退到TF-IDF
    
    def tfidf_search(self, query: str, k: int = 5, min_score: Optional[float] = None, 
                     max_candidates: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        传统TF-IDF关键词检索 (简化版本)
        """
        try:
            # 获取所有任务（不限制状态，只要有输出内容）
            all_tasks = default_repo.list_all_tasks()
            if not all_tasks:
                return []
            
            # 构建文档库
            documents = []
            task_map = {}
            
            for task in all_tasks:
                content = default_repo.get_task_output_content(task['id'])
                if content and content.strip():
                    documents.append(content)
                    task_map[len(documents) - 1] = task
            
            if not documents:
                return []
            
            # 简单的TF-IDF计算
            query_tokens = self._tokenize(query.lower())
            doc_scores = []
            
            # 计算IDF
            doc_freq = Counter()
            tokenized_docs = [self._tokenize(doc.lower()) for doc in documents]
            
            for doc_tokens in tokenized_docs:
                unique_tokens = set(doc_tokens)
                for token in unique_tokens:
                    doc_freq[token] += 1
            
            N = len(documents)
            
            # 为每个文档计算TF-IDF得分
            for i, doc_tokens in enumerate(tokenized_docs):
                score = 0.0
                doc_token_freq = Counter(doc_tokens)
                doc_len = len(doc_tokens)
                
                for token in query_tokens:
                    if token in doc_token_freq:
                        # TF: 词频 / 文档长度
                        tf = doc_token_freq[token] / doc_len if doc_len > 0 else 0
                        
                        # IDF: log((N + 1) / (1 + df)) + 1 (prevents negative values)
                        idf = math.log((N + 1) / (1 + doc_freq[token])) + 1 if doc_freq[token] > 0 else 1
                        
                        score += tf * idf
                
                min_tfidf_score = min_score if min_score is not None else self.min_tfidf_score
                if score >= min_tfidf_score:
                    task = task_map[i]
                    doc_scores.append({
                        'task_id': task['id'],
                        'name': task['name'],
                        'content': documents[i],
                        'similarity': score,
                        'tfidf_score': score
                    })
            
            # 按得分排序并返回top-k
            doc_scores.sort(key=lambda x: x['similarity'], reverse=True)
            results = doc_scores[:k]
            
            if self.debug:
                logger.debug(f"TF-IDF检索返回 {len(results)} 个结果")
            
            return results
            
        except Exception as e:
            logger.error(f"TF-IDF检索失败: {e}")
            return []
    
    def hybrid_search(self, query: str, k: int = 5, min_score: Optional[float] = None, 
                      max_candidates: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        混合检索：结合关键词匹配和语义相似度
        """
        try:
            # 1. 获取更多候选项进行关键词预筛选
            max_cand = max_candidates if max_candidates is not None else self.max_candidates
            candidate_k = min(k * 3, max_cand)
            
            # 2. TF-IDF快速筛选候选项
            tfidf_results = self.tfidf_search(query, candidate_k, min_score=min_score)
            
            if not tfidf_results:
                # 如果TF-IDF没有结果，尝试纯语义检索
                return self.semantic_search(query, k, min_score=min_score)
            
            # 3. 对候选项进行语义相似度计算
            query_embedding = self.embeddings_service.get_single_embedding(query)
            if not query_embedding:
                # 如果无法获取embedding，返回TF-IDF结果
                return tfidf_results[:k]
            
            # 4. 为每个候选项添加语义得分
            enhanced_results = []
            
            for result in tfidf_results:
                task_id = result['task_id']
                
                # 尝试获取已存储的embedding
                stored_embedding = default_repo.get_task_embedding(task_id)
                semantic_score = 0.0
                
                if stored_embedding and stored_embedding.get('embedding_vector'):
                    embedding = self.embeddings_service.json_to_embedding(
                        stored_embedding['embedding_vector']
                    )
                    if embedding:
                        semantic_score = self.embeddings_service.compute_similarity(
                            query_embedding, embedding
                        )
                else:
                    # 如果没有存储的embedding，临时计算
                    content = result.get('content', '')
                    if content.strip():
                        temp_embedding = self.embeddings_service.get_single_embedding(content)
                        if temp_embedding:
                            semantic_score = self.embeddings_service.compute_similarity(
                                query_embedding, temp_embedding
                            )
                
                # 5. 计算综合得分
                keyword_score = result.get('tfidf_score', 0.0)
                
                # 归一化处理
                normalized_keyword = min(keyword_score, 1.0)  # TF-IDF得分通常较小
                normalized_semantic = max(0.0, semantic_score)  # 确保非负
                
                # 加权组合
                final_score = (
                    self.keyword_weight * normalized_keyword + 
                    self.semantic_weight * normalized_semantic
                )
                
                # 过滤得分过低的结果
                if (normalized_keyword >= self.min_keyword_score or 
                    normalized_semantic >= self.min_semantic_score):
                    
                    result_copy = result.copy()
                    result_copy.update({
                        'similarity': final_score,
                        'semantic_score': semantic_score,
                        'keyword_score': keyword_score,
                        'final_score': final_score
                    })
                    enhanced_results.append(result_copy)
            
            # 6. 按综合得分排序
            enhanced_results.sort(key=lambda x: x['final_score'], reverse=True)
            final_results = enhanced_results[:k]
            
            if self.debug:
                logger.debug(f"混合检索返回 {len(final_results)} 个结果")
                for i, result in enumerate(final_results[:3]):  # 只显示前3个
                    logger.debug(f"  {i+1}. {result['name']} - "
                               f"综合: {result['final_score']:.3f}, "
                               f"关键词: {result.get('keyword_score', 0):.3f}, "
                               f"语义: {result.get('semantic_score', 0):.3f}")
            
            return final_results
            
        except Exception as e:
            logger.error(f"混合检索失败: {e}")
            # 回退到TF-IDF
            return self.tfidf_search(query, k, min_score=min_score, max_candidates=max_candidates)
    
    def _tokenize(self, text: str) -> List[str]:
        """
        简单分词器：支持中英文
        """
        # 移除标点符号，保留中文字符、英文字母和数字
        text = re.sub(r'[^\u4e00-\u9fff\w\s]', ' ', text)
        
        # 分离中英文
        tokens = []
        
        # 英文分词 (按空格)
        english_tokens = re.findall(r'[a-zA-Z0-9]+', text)
        tokens.extend([t.lower() for t in english_tokens if len(t) > 1])
        
        # 中文分词 (简单按字符)
        chinese_chars = re.findall(r'[\u4e00-\u9fff]', text)
        
        # 中文2-gram
        for i in range(len(chinese_chars) - 1):
            bigram = chinese_chars[i] + chinese_chars[i + 1]
            tokens.append(bigram)
        
        # 中文单字符 (仅对于较短的文本)
        if len(chinese_chars) <= 10:
            tokens.extend(chinese_chars)
        
        return [t for t in tokens if len(t) > 0]
    
    def get_retrieval_stats(self) -> Dict[str, Any]:
        """获取检索系统统计信息"""
        embedding_stats = default_repo.get_embedding_stats()
        
        return {
            "embedding_stats": embedding_stats,
            "retrieval_config": {
                "keyword_weight": self.keyword_weight,
                "semantic_weight": self.semantic_weight,
                "min_keyword_score": self.min_keyword_score,
                "min_semantic_score": self.min_semantic_score,
                "max_candidates": self.max_candidates
            },
            "embeddings_service": self.embeddings_service.get_service_info()
        }


# 全局单例
_retrieval_service = None

def get_retrieval_service() -> HybridRetrievalService:
    """获取混合检索服务单例"""
    global _retrieval_service
    if _retrieval_service is None:
        _retrieval_service = HybridRetrievalService()
    return _retrieval_service