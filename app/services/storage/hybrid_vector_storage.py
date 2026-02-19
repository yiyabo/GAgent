"""

supportSQLite ()  Milvus ()

"""

import sqlite3
import json
import asyncio
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from pathlib import Path

from .milvus_service import get_milvus_service
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class HybridVectorStorage:
    """"""

    def __init__(self, 
                 sqlite_path: str = "./data/databases/cache/embedding_cache.db",
                 migration_mode: str = "hybrid"):
        """


        Args:
            sqlite_path: SQLitedatabasepath
            migration_mode:  
                - "sqlite_only": SQLite
                - "hybrid": ()
                - "milvus_only": Milvus
        """
        self.sqlite_path = sqlite_path
        self.migration_mode = migration_mode
        self.milvus_service = None

        Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)

    async def initialize(self):
        """service"""
        try:
            logger.info(f"🚀  (: {self.migration_mode})")

            if self.migration_mode in ["hybrid", "milvus_only"]:
                self.milvus_service = await get_milvus_service()
                logger.info("✅ Milvusservice")

            if self.migration_mode in ["sqlite_only", "hybrid"]:
                self._test_sqlite_connection()
                logger.info("✅ SQLiteservice")

            logger.info("🎉 completed")
            return True

        except Exception as e:
            logger.error(f"❌ failed: {e}")
            return False

    def _test_sqlite_connection(self):
        """SQLiteconnection"""
        try:
            conn = sqlite3.connect(self.sqlite_path)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM embedding_cache")
            count = cursor.fetchone()[0]
            conn.close()
            logger.info(f"SQLiteconnection, : {count}")
        except Exception as e:
            logger.error(f"SQLiteconnectionfailed: {e}")
            raise

    async def store_embedding(self, 
                             text_hash: str, 
                             embedding: List[float], 
                             model: str,
                             force_sqlite_only: bool = False) -> bool:
        """


        Args:
            text_hash: 
            embedding: 
            model: modelname
            force_sqlite_only: SQLite
        """
        success_count = 0

        try:
            if (self.migration_mode in ["hybrid", "milvus_only"] and 
                not force_sqlite_only and 
                self.milvus_service):

                milvus_success = await self.milvus_service.store_embedding_cache(
                    text_hash, embedding, model
                )
                if milvus_success:
                    success_count += 1
                    logger.info(f"✅ Milvussuccess: {text_hash[:16]}...")

            if self.migration_mode in ["sqlite_only", "hybrid"]:
                sqlite_success = self._store_to_sqlite(text_hash, embedding, model)
                if sqlite_success:
                    success_count += 1
                    logger.info(f"✅ SQLitesuccess: {text_hash[:16]}...")

            return success_count > 0

        except Exception as e:
            logger.error(f"❌ failed: {e}")
            return False

    def _store_to_sqlite(self, text_hash: str, embedding: List[float], model: str) -> bool:
        """SQLite"""
        try:
            conn = sqlite3.connect(self.sqlite_path)
            cursor = conn.cursor()

            cursor.execute("SELECT access_count FROM embedding_cache WHERE text_hash = ?", (text_hash,))
            existing = cursor.fetchone()

            current_time = datetime.now().timestamp()

            if existing:
                new_count = existing[0] + 1
                cursor.execute("""
                    UPDATE embedding_cache 
                    SET access_count = ?, last_accessed = ?
                    WHERE text_hash = ?
                """, (new_count, current_time, text_hash))
            else:
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
            logger.error(f"SQLitefailed: {e}")
            return False

    async def search_similar(self, 
                           query_embedding: List[float],
                           top_k: int = 10,
                           score_threshold: float = 0.8,
                           prefer_milvus: bool = True) -> List[Dict[str, Any]]:
        """
        search

        Args:
            query_embedding: 
            top_k: count
            score_threshold: 
            prefer_milvus: Milvus
        """
        try:
            if (prefer_milvus and 
                self.migration_mode in ["hybrid", "milvus_only"] and 
                self.milvus_service):

                start_time = datetime.now()
                results = await self.milvus_service.search_embedding_cache(
                    query_embedding, top_k, score_threshold
                )
                search_time = (datetime.now() - start_time).total_seconds() * 1000

                logger.info(f"🚀 Milvussearchcompleted: {len(results)}result, {search_time:.2f}ms")

                if results:
                    return results

            if self.migration_mode in ["sqlite_only", "hybrid"]:
                start_time = datetime.now()
                results = self._search_sqlite(query_embedding, top_k, score_threshold)
                search_time = (datetime.now() - start_time).total_seconds() * 1000

                logger.info(f"📦 SQLitesearchcompleted: {len(results)}result, {search_time:.2f}ms")
                return results

            return []

        except Exception as e:
            logger.error(f"❌ searchfailed: {e}")
            return []

    def _search_sqlite(self, 
                      query_embedding: List[float], 
                      top_k: int, 
                      score_threshold: float) -> List[Dict[str, Any]]:
        """SQLitesearch ()"""
        try:
            conn = sqlite3.connect(self.sqlite_path)
            cursor = conn.cursor()

            cursor.execute("SELECT text_hash, embedding_json, model, created_at, access_count FROM embedding_cache")
            all_embeddings = cursor.fetchall()
            conn.close()

            if not all_embeddings:
                return []

            import numpy as np
            query_vec = np.array(query_embedding)
            query_norm = np.linalg.norm(query_vec)

            similarities = []
            for text_hash, embedding_json, model, created_at, access_count in all_embeddings:
                try:
                    stored_vec = np.array(json.loads(embedding_json))
                    stored_norm = np.linalg.norm(stored_vec)

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
                    logger.warning(f"failed: {e}")
                    continue

            similarities.sort(key=lambda x: x["score"], reverse=True)
            return similarities[:top_k]

        except Exception as e:
            logger.error(f"SQLitesearchfailed: {e}")
            return []

    async def get_storage_stats(self) -> Dict[str, Any]:
        """Return storage backend statistics for SQLite and Milvus."""
        stats = {
            "migration_mode": self.migration_mode,
            "sqlite": {},
            "milvus": {}
        }

        try:
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

            if (self.migration_mode in ["hybrid", "milvus_only"] and 
                self.milvus_service):
                milvus_stats = await self.milvus_service.get_collection_stats()
                stats["milvus"] = milvus_stats

            return stats

        except Exception as e:
            logger.error(f"Failed to get storage statistics: {e}")
            return stats

    async def migrate_from_sqlite_to_milvus(self) -> Dict[str, Any]:
        """Migrate cached embeddings from SQLite to Milvus."""
        if not self.milvus_service:
            return {"success": False, "error": "Milvus service is not available"}

        try:
            logger.info("🚀 SQLiteMilvus...")

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
                        logger.info(f"✅ success: {text_hash[:16]}... ({migrated_count}/{len(sqlite_data)})")
                    else:
                        failed_count += 1

                except Exception as e:
                    logger.error(f"failed {text_hash}: {e}")
                    failed_count += 1

            result = {
                "success": True,
                "total_records": len(sqlite_data),
                "migrated_count": migrated_count,
                "failed_count": failed_count,
                "migration_rate": migrated_count / len(sqlite_data) if sqlite_data else 0
            }

            logger.info(f"🎉 completed: {migrated_count}/{len(sqlite_data)} success")
            return result

        except Exception as e:
            logger.error(f"❌ failed: {e}")
            return {"success": False, "error": str(e)}


_hybrid_storage = None

async def get_hybrid_storage(migration_mode: str = "hybrid") -> HybridVectorStorage:
    """get()"""
    global _hybrid_storage

    if _hybrid_storage is None:
        _hybrid_storage = HybridVectorStorage(migration_mode=migration_mode)
        await _hybrid_storage.initialize()

    return _hybrid_storage
