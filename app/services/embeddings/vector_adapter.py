"""
向量存储适配器
将现有的嵌入服务无缝迁移到新的Milvus混合存储系统
"""

import json
import hashlib
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime
import logging

from ..storage.hybrid_vector_storage import get_hybrid_storage
from .cache import EmbeddingCache  # 原有的缓存系统

logger = logging.getLogger(__name__)

class VectorStorageAdapter:
    """向量存储适配器 - 桥接新旧系统"""
    
    def __init__(self, migration_mode: str = "hybrid"):
        """
        初始化适配器
        
        Args:
            migration_mode: 迁移模式
                - "legacy": 仅使用原有SQLite系统
                - "hybrid": 双写模式，逐步迁移
                - "milvus": 仅使用新的Milvus系统
        """
        self.migration_mode = migration_mode
        self.hybrid_storage = None
        self.legacy_cache = None
        
        # 统计信息
        self.stats = {
            "total_requests": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "milvus_operations": 0,
            "sqlite_operations": 0
        }
    
    async def initialize(self):
        """初始化适配器"""
        try:
            logger.info(f"🚀 初始化向量存储适配器 (模式: {self.migration_mode})")
            
            # 初始化新的混合存储
            if self.migration_mode in ["hybrid", "milvus"]:
                self.hybrid_storage = await get_hybrid_storage(self.migration_mode)
                logger.info("✅ Milvus混合存储已就绪")
            
            # 初始化原有缓存系统
            if self.migration_mode in ["legacy", "hybrid"]:
                self.legacy_cache = EmbeddingCache()
                logger.info("✅ 原有SQLite缓存已就绪")
            
            logger.info("🎉 向量存储适配器初始化完成")
            return True
            
        except Exception as e:
            logger.error(f"❌ 适配器初始化失败: {e}")
            return False
    
    def _compute_text_hash(self, text: str, model: str) -> str:
        """计算文本哈希值 (兼容原有系统)"""
        combined = f"{text}:{model}"
        return hashlib.sha256(combined.encode()).hexdigest()
    
    async def get_embedding(self, text: str, model: str = "embedding-3") -> Optional[List[float]]:
        """
        获取嵌入向量 (智能路由)
        
        Args:
            text: 输入文本
            model: 模型名称
            
        Returns:
            嵌入向量或None
        """
        self.stats["total_requests"] += 1
        text_hash = self._compute_text_hash(text, model)
        
        try:
            # 1. 尝试从新的Milvus系统获取
            if self.hybrid_storage and self.migration_mode in ["hybrid", "milvus"]:
                milvus_result = await self._get_from_milvus(text_hash)
                if milvus_result:
                    self.stats["cache_hits"] += 1
                    self.stats["milvus_operations"] += 1
                    logger.debug(f"✅ Milvus缓存命中: {text_hash[:16]}...")
                    return milvus_result
            
            # 2. 回退到原有SQLite系统
            if self.legacy_cache and self.migration_mode in ["legacy", "hybrid"]:
                legacy_result = await self._get_from_legacy(text, model)
                if legacy_result:
                    self.stats["cache_hits"] += 1
                    self.stats["sqlite_operations"] += 1
                    logger.debug(f"✅ SQLite缓存命中: {text_hash[:16]}...")
                    
                    # 如果是混合模式，将数据同步到Milvus
                    if self.hybrid_storage and self.migration_mode == "hybrid":
                        await self._sync_to_milvus(text_hash, legacy_result, model)
                    
                    return legacy_result
            
            # 3. 缓存未命中
            self.stats["cache_misses"] += 1
            logger.debug(f"❌ 缓存未命中: {text_hash[:16]}...")
            return None
            
        except Exception as e:
            logger.error(f"❌ 获取嵌入向量失败: {e}")
            return None
    
    async def store_embedding(self, text: str, embedding: List[float], model: str = "embedding-3") -> bool:
        """
        存储嵌入向量 (双写模式)
        
        Args:
            text: 输入文本
            embedding: 嵌入向量
            model: 模型名称
            
        Returns:
            存储是否成功
        """
        text_hash = self._compute_text_hash(text, model)
        success_count = 0
        
        try:
            # 1. 存储到新的Milvus系统
            if self.hybrid_storage and self.migration_mode in ["hybrid", "milvus"]:
                milvus_success = await self.hybrid_storage.store_embedding(
                    text_hash, embedding, model
                )
                if milvus_success:
                    success_count += 1
                    self.stats["milvus_operations"] += 1
                    logger.debug(f"✅ Milvus存储成功: {text_hash[:16]}...")
            
            # 2. 存储到原有SQLite系统
            if self.legacy_cache and self.migration_mode in ["legacy", "hybrid"]:
                legacy_success = await self._store_to_legacy(text, embedding, model)
                if legacy_success:
                    success_count += 1
                    self.stats["sqlite_operations"] += 1
                    logger.debug(f"✅ SQLite存储成功: {text_hash[:16]}...")
            
            return success_count > 0
            
        except Exception as e:
            logger.error(f"❌ 存储嵌入向量失败: {e}")
            return False
    
    async def search_similar(self, query_embedding: List[float], top_k: int = 10, 
                           score_threshold: float = 0.8) -> List[Dict[str, Any]]:
        """
        搜索相似向量
        
        Args:
            query_embedding: 查询向量
            top_k: 返回数量
            score_threshold: 相似度阈值
            
        Returns:
            相似向量列表
        """
        try:
            # 优先使用新的Milvus系统
            if self.hybrid_storage and self.migration_mode in ["hybrid", "milvus"]:
                results = await self.hybrid_storage.search_similar(
                    query_embedding, top_k, score_threshold, prefer_milvus=True
                )
                self.stats["milvus_operations"] += 1
                
                if results:
                    logger.debug(f"✅ Milvus搜索: {len(results)}个结果")
                    return results
            
            # 回退到SQLite系统 (简化实现)
            if self.legacy_cache and self.migration_mode in ["legacy", "hybrid"]:
                # 注意：原有系统可能没有向量搜索功能，这里是占位符
                logger.info("📦 SQLite向量搜索功能有限，建议使用Milvus")
                self.stats["sqlite_operations"] += 1
                return []
            
            return []
            
        except Exception as e:
            logger.error(f"❌ 向量搜索失败: {e}")
            return []
    
    async def _get_from_milvus(self, text_hash: str) -> Optional[List[float]]:
        """从Milvus获取向量"""
        try:
            # 这里需要实现从Milvus根据text_hash查询向量的逻辑
            # 当前的Milvus系统主要支持向量搜索，精确查询需要额外实现
            return None
        except Exception as e:
            logger.error(f"Milvus查询失败: {e}")
            return None
    
    async def _get_from_legacy(self, text: str, model: str) -> Optional[List[float]]:
        """从原有系统获取向量"""
        try:
            # 使用原有缓存系统的接口
            result = self.legacy_cache.get(text, model)
            if result and hasattr(result, 'embedding'):
                return result.embedding
            return None
        except Exception as e:
            logger.error(f"SQLite查询失败: {e}")
            return None
    
    async def _store_to_legacy(self, text: str, embedding: List[float], model: str) -> bool:
        """存储到原有系统"""
        try:
            # 使用原有缓存系统的接口
            self.legacy_cache.put(text, embedding, model)
            return True
        except Exception as e:
            logger.error(f"SQLite存储失败: {e}")
            return False
    
    async def _sync_to_milvus(self, text_hash: str, embedding: List[float], model: str) -> bool:
        """将数据同步到Milvus"""
        try:
            if self.hybrid_storage:
                return await self.hybrid_storage.store_embedding(text_hash, embedding, model)
            return False
        except Exception as e:
            logger.error(f"同步到Milvus失败: {e}")
            return False
    
    async def get_stats(self) -> Dict[str, Any]:
        """获取适配器统计信息"""
        try:
            base_stats = self.stats.copy()
            
            # 添加存储系统统计
            if self.hybrid_storage:
                storage_stats = await self.hybrid_storage.get_storage_stats()
                base_stats["storage_stats"] = storage_stats
            
            # 计算命中率
            total_requests = base_stats["total_requests"]
            if total_requests > 0:
                base_stats["cache_hit_rate"] = base_stats["cache_hits"] / total_requests
            else:
                base_stats["cache_hit_rate"] = 0.0
            
            return base_stats
            
        except Exception as e:
            logger.error(f"获取统计信息失败: {e}")
            return self.stats
    
    async def migrate_legacy_data(self) -> Dict[str, Any]:
        """迁移原有数据到新系统"""
        if not self.hybrid_storage or self.migration_mode == "legacy":
            return {"success": False, "error": "Milvus系统未启用"}
        
        try:
            logger.info("🚀 开始迁移原有数据到Milvus...")
            
            # 执行迁移
            result = await self.hybrid_storage.migrate_from_sqlite_to_milvus()
            
            logger.info(f"🎉 数据迁移完成: {result}")
            return result
            
        except Exception as e:
            logger.error(f"❌ 数据迁移失败: {e}")
            return {"success": False, "error": str(e)}


# 全局适配器实例
_vector_adapter = None

async def get_vector_adapter(migration_mode: str = "hybrid") -> VectorStorageAdapter:
    """获取向量存储适配器实例（单例模式）"""
    global _vector_adapter
    
    if _vector_adapter is None:
        _vector_adapter = VectorStorageAdapter(migration_mode)
        await _vector_adapter.initialize()
    
    return _vector_adapter

async def migrate_embeddings_service():
    """迁移现有嵌入服务到新的向量存储系统"""
    logger.info("🔄 开始迁移嵌入服务到新的向量存储系统...")
    
    try:
        # 初始化适配器
        adapter = await get_vector_adapter("hybrid")
        
        # 执行数据迁移
        migration_result = await adapter.migrate_legacy_data()
        
        logger.info(f"✅ 嵌入服务迁移完成: {migration_result}")
        return migration_result
        
    except Exception as e:
        logger.error(f"❌ 嵌入服务迁移失败: {e}")
        return {"success": False, "error": str(e)}
