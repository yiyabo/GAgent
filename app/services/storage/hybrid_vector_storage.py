"""
混合向量存储管理器
同时支持SQLite (备份) 和 Milvus (主力)
提供无缝迁移和回滚能力
"""

import sqlite3
import json
import asyncio
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from pathlib import Path

from .milvus_service import get_milvus_service
import logging

# 简化日志配置
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class HybridVectorStorage:
    """混合向量存储管理器"""
    
    def __init__(self, 
                 sqlite_path: str = "./data/databases/cache/embedding_cache.db",
                 migration_mode: str = "hybrid"):
        """
        初始化混合存储
        
        Args:
            sqlite_path: SQLite数据库路径
            migration_mode: 迁移模式 
                - "sqlite_only": 仅使用SQLite
                - "hybrid": 双写模式（推荐）
                - "milvus_only": 仅使用Milvus
        """
        self.sqlite_path = sqlite_path
        self.migration_mode = migration_mode
        self.milvus_service = None
        
        # 确保SQLite数据库存在
        Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
    
    async def initialize(self):
        """初始化存储服务"""
        try:
            logger.info(f"🚀 初始化混合向量存储 (模式: {self.migration_mode})")
            
            # 初始化Milvus服务
            if self.migration_mode in ["hybrid", "milvus_only"]:
                self.milvus_service = await get_milvus_service()
                logger.info("✅ Milvus服务已就绪")
            
            # 检查SQLite连接
            if self.migration_mode in ["sqlite_only", "hybrid"]:
                self._test_sqlite_connection()
                logger.info("✅ SQLite服务已就绪")
            
            logger.info("🎉 混合向量存储初始化完成")
            return True
            
        except Exception as e:
            logger.error(f"❌ 混合存储初始化失败: {e}")
            return False
    
    def _test_sqlite_connection(self):
        """测试SQLite连接"""
        try:
            conn = sqlite3.connect(self.sqlite_path)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM embedding_cache")
            count = cursor.fetchone()[0]
            conn.close()
            logger.info(f"SQLite连接正常，当前记录数: {count}")
        except Exception as e:
            logger.error(f"SQLite连接测试失败: {e}")
            raise
    
    async def store_embedding(self, 
                             text_hash: str, 
                             embedding: List[float], 
                             model: str,
                             force_sqlite_only: bool = False) -> bool:
        """
        存储嵌入向量
        
        Args:
            text_hash: 文本哈希
            embedding: 向量数据
            model: 模型名称
            force_sqlite_only: 强制仅使用SQLite
        """
        success_count = 0
        
        try:
            # 1. 存储到Milvus (如果启用)
            if (self.migration_mode in ["hybrid", "milvus_only"] and 
                not force_sqlite_only and 
                self.milvus_service):
                
                milvus_success = await self.milvus_service.store_embedding_cache(
                    text_hash, embedding, model
                )
                if milvus_success:
                    success_count += 1
                    logger.info(f"✅ Milvus存储成功: {text_hash[:16]}...")
            
            # 2. 存储到SQLite (如果启用)
            if self.migration_mode in ["sqlite_only", "hybrid"]:
                sqlite_success = self._store_to_sqlite(text_hash, embedding, model)
                if sqlite_success:
                    success_count += 1
                    logger.info(f"✅ SQLite存储成功: {text_hash[:16]}...")
            
            return success_count > 0
            
        except Exception as e:
            logger.error(f"❌ 存储嵌入向量失败: {e}")
            return False
    
    def _store_to_sqlite(self, text_hash: str, embedding: List[float], model: str) -> bool:
        """存储到SQLite"""
        try:
            conn = sqlite3.connect(self.sqlite_path)
            cursor = conn.cursor()
            
            # 检查是否已存在
            cursor.execute("SELECT access_count FROM embedding_cache WHERE text_hash = ?", (text_hash,))
            existing = cursor.fetchone()
            
            current_time = datetime.now().timestamp()
            
            if existing:
                # 更新访问计数
                new_count = existing[0] + 1
                cursor.execute("""
                    UPDATE embedding_cache 
                    SET access_count = ?, last_accessed = ?
                    WHERE text_hash = ?
                """, (new_count, current_time, text_hash))
            else:
                # 插入新记录
                cursor.execute("""
                    INSERT INTO embedding_cache 
                    (text_hash, embedding_json, model, created_at, access_count, last_accessed)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    text_hash,
                    json.dumps(embedding),
                    model,
                    current_time,
                    1,
                    current_time
                ))
            
            conn.commit()
            conn.close()
            return True
            
        except Exception as e:
            logger.error(f"SQLite存储失败: {e}")
            return False
    
    async def search_similar(self, 
                           query_embedding: List[float],
                           top_k: int = 10,
                           score_threshold: float = 0.8,
                           prefer_milvus: bool = True) -> List[Dict[str, Any]]:
        """
        搜索相似向量
        
        Args:
            query_embedding: 查询向量
            top_k: 返回数量
            score_threshold: 相似度阈值
            prefer_milvus: 优先使用Milvus
        """
        try:
            # 优先使用Milvus搜索 (性能更好)
            if (prefer_milvus and 
                self.migration_mode in ["hybrid", "milvus_only"] and 
                self.milvus_service):
                
                start_time = datetime.now()
                results = await self.milvus_service.search_embedding_cache(
                    query_embedding, top_k, score_threshold
                )
                search_time = (datetime.now() - start_time).total_seconds() * 1000
                
                logger.info(f"🚀 Milvus搜索完成: {len(results)}个结果, 耗时{search_time:.2f}ms")
                
                if results:
                    return results
            
            # 回退到SQLite搜索
            if self.migration_mode in ["sqlite_only", "hybrid"]:
                start_time = datetime.now()
                results = self._search_sqlite(query_embedding, top_k, score_threshold)
                search_time = (datetime.now() - start_time).total_seconds() * 1000
                
                logger.info(f"📦 SQLite搜索完成: {len(results)}个结果, 耗时{search_time:.2f}ms")
                return results
            
            return []
            
        except Exception as e:
            logger.error(f"❌ 向量搜索失败: {e}")
            return []
    
    def _search_sqlite(self, 
                      query_embedding: List[float], 
                      top_k: int, 
                      score_threshold: float) -> List[Dict[str, Any]]:
        """SQLite向量搜索 (简化的余弦相似度)"""
        try:
            conn = sqlite3.connect(self.sqlite_path)
            cursor = conn.cursor()
            
            # 获取所有向量
            cursor.execute("SELECT text_hash, embedding_json, model, created_at, access_count FROM embedding_cache")
            all_embeddings = cursor.fetchall()
            conn.close()
            
            if not all_embeddings:
                return []
            
            # 计算相似度
            import numpy as np
            query_vec = np.array(query_embedding)
            query_norm = np.linalg.norm(query_vec)
            
            similarities = []
            for text_hash, embedding_json, model, created_at, access_count in all_embeddings:
                try:
                    stored_vec = np.array(json.loads(embedding_json))
                    stored_norm = np.linalg.norm(stored_vec)
                    
                    # 余弦相似度
                    similarity = np.dot(query_vec, stored_vec) / (query_norm * stored_norm)
                    
                    if similarity >= score_threshold:
                        similarities.append({
                            "text_hash": text_hash,
                            "model": model,
                            "score": float(similarity),
                            "created_at": int(created_at),
                            "access_count": access_count
                        })
                except Exception as e:
                    logger.warning(f"向量计算失败: {e}")
                    continue
            
            # 按相似度排序
            similarities.sort(key=lambda x: x["score"], reverse=True)
            return similarities[:top_k]
            
        except Exception as e:
            logger.error(f"SQLite搜索失败: {e}")
            return []
    
    async def get_storage_stats(self) -> Dict[str, Any]:
        """获取存储统计信息"""
        stats = {
            "migration_mode": self.migration_mode,
            "sqlite": {},
            "milvus": {}
        }
        
        try:
            # SQLite统计
            if self.migration_mode in ["sqlite_only", "hybrid"]:
                conn = sqlite3.connect(self.sqlite_path)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM embedding_cache")
                sqlite_count = cursor.fetchone()[0]
                conn.close()
                
                stats["sqlite"] = {
                    "record_count": sqlite_count,
                    "file_path": self.sqlite_path,
                    "status": "active"
                }
            
            # Milvus统计
            if (self.migration_mode in ["hybrid", "milvus_only"] and 
                self.milvus_service):
                milvus_stats = await self.milvus_service.get_collection_stats()
                stats["milvus"] = milvus_stats
            
            return stats
            
        except Exception as e:
            logger.error(f"获取存储统计失败: {e}")
            return stats
    
    async def migrate_from_sqlite_to_milvus(self) -> Dict[str, Any]:
        """从SQLite迁移数据到Milvus"""
        if not self.milvus_service:
            return {"success": False, "error": "Milvus服务未初始化"}
        
        try:
            logger.info("🚀 开始SQLite到Milvus的数据迁移...")
            
            # 读取SQLite数据
            conn = sqlite3.connect(self.sqlite_path)
            cursor = conn.cursor()
            cursor.execute("SELECT text_hash, embedding_json, model, created_at, access_count FROM embedding_cache")
            sqlite_data = cursor.fetchall()
            conn.close()
            
            migrated_count = 0
            failed_count = 0
            
            for text_hash, embedding_json, model, created_at, access_count in sqlite_data:
                try:
                    embedding = json.loads(embedding_json)
                    
                    success = await self.milvus_service.store_embedding_cache(
                        text_hash, embedding, model
                    )
                    
                    if success:
                        migrated_count += 1
                        logger.info(f"✅ 迁移成功: {text_hash[:16]}... ({migrated_count}/{len(sqlite_data)})")
                    else:
                        failed_count += 1
                        
                except Exception as e:
                    logger.error(f"迁移记录失败 {text_hash}: {e}")
                    failed_count += 1
            
            result = {
                "success": True,
                "total_records": len(sqlite_data),
                "migrated_count": migrated_count,
                "failed_count": failed_count,
                "migration_rate": migrated_count / len(sqlite_data) if sqlite_data else 0
            }
            
            logger.info(f"🎉 迁移完成: {migrated_count}/{len(sqlite_data)} 条记录迁移成功")
            return result
            
        except Exception as e:
            logger.error(f"❌ 数据迁移失败: {e}")
            return {"success": False, "error": str(e)}


# 全局混合存储实例
_hybrid_storage = None

async def get_hybrid_storage(migration_mode: str = "hybrid") -> HybridVectorStorage:
    """获取混合存储实例（单例模式）"""
    global _hybrid_storage
    
    if _hybrid_storage is None:
        _hybrid_storage = HybridVectorStorage(migration_mode=migration_mode)
        await _hybrid_storage.initialize()
    
    return _hybrid_storage
