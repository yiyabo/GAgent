"""

serviceMilvussystem
"""

import json
import hashlib
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime
import logging

from ..storage.hybrid_vector_storage import get_hybrid_storage
from .cache import EmbeddingCache  # system

logger = logging.getLogger(__name__)

class VectorStorageAdapter:
    """ - system"""

    def __init__(self, migration_mode: str = "hybrid"):
        """


        Args:
            migration_mode: 
                - "legacy": SQLitesystem
                - "hybrid": , 
                - "milvus": Milvussystem
        """
        self.migration_mode = migration_mode
        self.hybrid_storage = None
        self.legacy_cache = None

        self.stats = {
            "total_requests": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "milvus_operations": 0,
            "sqlite_operations": 0
        }

    async def initialize(self):
        """"""
        try:
            logger.info(f"🚀  (: {self.migration_mode})")

            if self.migration_mode in ["hybrid", "milvus"]:
                self.hybrid_storage = await get_hybrid_storage(self.migration_mode)
                logger.info("✅ Milvus")

            if self.migration_mode in ["legacy", "hybrid"]:
                self.legacy_cache = EmbeddingCache()
                logger.info("✅ SQLite")

            logger.info("🎉 completed")
            return True

        except Exception as e:
            logger.error(f"❌ failed: {e}")
            return False

    def _compute_text_hash(self, text: str, model: str) -> str:
        """ (system)"""
        combined = f"{text}:{model}"
        return hashlib.sha256(combined.encode()).hexdigest()

    async def get_embedding(self, text: str, model: str = "embedding-3") -> Optional[List[float]]:
        """
        get ()

        Args:
            text: input
            model: modelname

        Returns:
            None
        """
        self.stats["total_requests"] += 1
        text_hash = self._compute_text_hash(text, model)

        try:
            if self.hybrid_storage and self.migration_mode in ["hybrid", "milvus"]:
                milvus_result = await self._get_from_milvus(text_hash)
                if milvus_result:
                    self.stats["cache_hits"] += 1
                    self.stats["milvus_operations"] += 1
                    logger.debug(f"Milvus cache hit: {text_hash[:16]}...")
                    return milvus_result

            if self.legacy_cache and self.migration_mode in ["legacy", "hybrid"]:
                legacy_result = await self._get_from_legacy(text, model)
                if legacy_result:
                    self.stats["cache_hits"] += 1
                    self.stats["sqlite_operations"] += 1
                    logger.debug(f"SQLite cache hit: {text_hash[:16]}...")

                    if self.hybrid_storage and self.migration_mode == "hybrid":
                        await self._sync_to_milvus(text_hash, legacy_result, model)

                    return legacy_result

            self.stats["cache_misses"] += 1
            logger.debug(f"Cache miss: {text_hash[:16]}...")
            return None

        except Exception as e:
            logger.error(f"Failed to get embedding from cache: {e}")
            return None

    async def store_embedding(self, text: str, embedding: List[float], model: str = "embedding-3") -> bool:
        """
        ()

        Args:
            text: input
            embedding: 
            model: modelname

        Returns:
            success
        """
        text_hash = self._compute_text_hash(text, model)
        success_count = 0

        try:
            if self.hybrid_storage and self.migration_mode in ["hybrid", "milvus"]:
                milvus_success = await self.hybrid_storage.store_embedding(
                    text_hash, embedding, model
                )
                if milvus_success:
                    success_count += 1
                    self.stats["milvus_operations"] += 1
                    logger.debug(f"✅ Milvussuccess: {text_hash[:16]}...")

            if self.legacy_cache and self.migration_mode in ["legacy", "hybrid"]:
                legacy_success = await self._store_to_legacy(text, embedding, model)
                if legacy_success:
                    success_count += 1
                    self.stats["sqlite_operations"] += 1
                    logger.debug(f"✅ SQLitesuccess: {text_hash[:16]}...")

            return success_count > 0

        except Exception as e:
            logger.error(f"❌ failed: {e}")
            return False

    async def search_similar(self, query_embedding: List[float], top_k: int = 10, 
                           score_threshold: float = 0.8) -> List[Dict[str, Any]]:
        """
        search

        Args:
            query_embedding: 
            top_k: count
            score_threshold: 

        Returns:

        """
        try:
            if self.hybrid_storage and self.migration_mode in ["hybrid", "milvus"]:
                results = await self.hybrid_storage.search_similar(
                    query_embedding, top_k, score_threshold, prefer_milvus=True
                )
                self.stats["milvus_operations"] += 1

                if results:
                    logger.debug(f"✅ Milvussearch: {len(results)}result")
                    return results

            if self.legacy_cache and self.migration_mode in ["legacy", "hybrid"]:
                logger.info("📦 SQLitesearch, recommendationMilvus")
                self.stats["sqlite_operations"] += 1
                return []

            return []

        except Exception as e:
            logger.error(f"❌ searchfailed: {e}")
            return []

    async def _get_from_milvus(self, text_hash: str) -> Optional[List[float]]:
        """Milvusget"""
        try:
            return None
        except Exception as e:
            logger.error(f"Milvusfailed: {e}")
            return None

    async def _get_from_legacy(self, text: str, model: str) -> Optional[List[float]]:
        """systemget"""
        try:
            result = self.legacy_cache.get(text, model)
            if result and hasattr(result, 'embedding'):
                return result.embedding
            return None
        except Exception as e:
            logger.error(f"SQLitefailed: {e}")
            return None

    async def _store_to_legacy(self, text: str, embedding: List[float], model: str) -> bool:
        """system"""
        try:
            self.legacy_cache.put(text, embedding, model)
            return True
        except Exception as e:
            logger.error(f"SQLitefailed: {e}")
            return False

    async def _sync_to_milvus(self, text_hash: str, embedding: List[float], model: str) -> bool:
        """Milvus"""
        try:
            if self.hybrid_storage:
                return await self.hybrid_storage.store_embedding(text_hash, embedding, model)
            return False
        except Exception as e:
            logger.error(f"Milvusfailed: {e}")
            return False

    async def get_stats(self) -> Dict[str, Any]:
        """Return adapter statistics and storage backend metrics."""
        try:
            base_stats = self.stats.copy()

            if self.hybrid_storage:
                storage_stats = await self.hybrid_storage.get_storage_stats()
                base_stats["storage_stats"] = storage_stats

            total_requests = base_stats["total_requests"]
            if total_requests > 0:
                base_stats["cache_hit_rate"] = base_stats["cache_hits"] / total_requests
            else:
                base_stats["cache_hit_rate"] = 0.0

            return base_stats

        except Exception as e:
            logger.error(f"Failed to get vector adapter statistics: {e}")
            return self.stats

    async def migrate_legacy_data(self) -> Dict[str, Any]:
        """Migrate legacy embedding cache data into current storage backend."""
        if not self.hybrid_storage or self.migration_mode == "legacy":
            return {"success": False, "error": "Milvus migration is unavailable in legacy mode"}

        try:
            logger.info("Starting Milvus migration...")

            result = await self.hybrid_storage.migrate_from_sqlite_to_milvus()

            logger.info(f"Migration completed: {result}")
            return result

        except Exception as e:
            logger.error(f"Migration failed: {e}")
            return {"success": False, "error": str(e)}


_vector_adapter = None

async def get_vector_adapter(migration_mode: str = "hybrid") -> VectorStorageAdapter:
    """get()"""
    global _vector_adapter

    if _vector_adapter is None:
        _vector_adapter = VectorStorageAdapter(migration_mode)
        await _vector_adapter.initialize()

    return _vector_adapter

async def migrate_embeddings_service():
    """servicesystem"""
    logger.info("🔄 servicesystem...")

    try:
        adapter = await get_vector_adapter("hybrid")

        migration_result = await adapter.migrate_legacy_data()

        logger.info(f"✅ servicecompleted: {migration_result}")
        return migration_result

    except Exception as e:
        logger.error(f"❌ servicefailed: {e}")
        return {"success": False, "error": str(e)}
