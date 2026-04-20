"""
Milvusdatabaseservice
support, Dockerservice
"""

import os
import json
import numpy as np
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
import asyncio
from pathlib import Path

try:
    from pymilvus import (
        MilvusClient, 
        DataType,
        connections
    )
    PYMILVUS_AVAILABLE = True
except ImportError:
    PYMILVUS_AVAILABLE = False
    MilvusClient = None
    DataType = None
    connections = None

import logging

logger = logging.getLogger(__name__)

class MilvusVectorService:
    """Milvusservice"""

    def __init__(self, uri: str = "data/milvus/milvus_demo.db", token: str = ""):
        if not PYMILVUS_AVAILABLE:
            logger.warning("pymilvusunavailable, MilvusVectorService")
            self.client = None
            return

        """
        Milvusservice

        Args:
            uri: MilvusserviceURI
            token: MilvusserviceToken
        """
        self.uri = uri
        self.token = token
        self.client = None
        self.collections = {
            "embedding_cache": "embedding_cache_collection",
            "task_embeddings": "task_embeddings_collection"
        }

        Path(uri).parent.mkdir(parents=True, exist_ok=True)

    async def initialize(self):
        """Milvus"""
        try:
            logger.info(f"🚀 Milvus Lite: {self.uri}")

            self.client = MilvusClient(uri=self.uri)

            await self._create_collections()

            logger.info("✅ Milvuscompleted")
            return True

        except Exception as e:
            logger.error(f"❌ Milvusfailed: {e}")
            return False

    async def _create_collections(self):
        """create"""

        await self._create_embedding_cache_collection()

        await self._create_task_embeddings_collection()

    async def _create_embedding_cache_collection(self):
        """create"""
        collection_name = self.collections["embedding_cache"]

        if self.client.has_collection(collection_name):
            logger.info(f" {collection_name} ")
            return

        schema = MilvusClient.create_schema(
            auto_id=True,
            enable_dynamic_field=False
        )

        schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True)
        schema.add_field(field_name="text_hash", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="embedding", datatype=DataType.FLOAT_VECTOR, dim=1536)
        schema.add_field(field_name="model", datatype=DataType.VARCHAR, max_length=50)
        schema.add_field(field_name="created_at", datatype=DataType.INT64)
        schema.add_field(field_name="access_count", datatype=DataType.INT64)
        schema.add_field(field_name="last_accessed", datatype=DataType.INT64)

        self.client.create_collection(
            collection_name=collection_name,
            schema=schema,
            metric_type="COSINE",  # 
            consistency_level="Strong"
        )

        index_params = self.client.prepare_index_params()
        index_params.add_index(
            field_name="embedding",
            index_type="IVF_FLAT",  # Litesupporttype
            metric_type="COSINE",
            params={"nlist": 128}  # IVF_FLATparameter
        )

        self.client.create_index(
            collection_name=collection_name,
            index_params=index_params
        )

        logger.info(f"✅ create: {collection_name}")

    async def _create_task_embeddings_collection(self):
        """Create task embedding collection."""
        collection_name = self.collections["task_embeddings"]

        if self.client.has_collection(collection_name):
            logger.info("Collection already exists: %s", collection_name)
            return

        schema = MilvusClient.create_schema(
            auto_id=True,
            enable_dynamic_field=False
        )

        schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True)
        schema.add_field(field_name="task_id", datatype=DataType.INT64)
        schema.add_field(field_name="embedding", datatype=DataType.FLOAT_VECTOR, dim=1536)
        schema.add_field(field_name="model", datatype=DataType.VARCHAR, max_length=50)
        schema.add_field(field_name="created_at", datatype=DataType.INT64)
        schema.add_field(field_name="updated_at", datatype=DataType.INT64)

        self.client.create_collection(
            collection_name=collection_name,
            schema=schema,
            metric_type="COSINE",
            consistency_level="Strong"
        )

        index_params = self.client.prepare_index_params()
        index_params.add_index(
            field_name="embedding",
            index_type="IVF_FLAT",  # Litesupporttype
            metric_type="COSINE",
            params={"nlist": 128}  # IVF_FLATparameter
        )

        self.client.create_index(
            collection_name=collection_name,
            index_params=index_params
        )

        logger.info(f"✅ create: {collection_name}")

    async def store_embedding_cache(
        self, 
        text_hash: str, 
        embedding: List[float], 
        model: str
    ) -> bool:
        """"""
        try:
            collection_name = self.collections["embedding_cache"]

            data = [{
                "text_hash": text_hash,
                "embedding": embedding,
                "model": model,
                "created_at": int(datetime.now().timestamp()),
                "access_count": 1,
                "last_accessed": int(datetime.now().timestamp())
            }]

            result = self.client.insert(
                collection_name=collection_name,
                data=data
            )

            logger.info("Stored embedding cache entry: %s...", text_hash[:16])
            return True

        except Exception as e:
            logger.error("Failed to store embedding cache: %s", e)
            return False

    async def store_task_embedding(
        self,
        task_id: int,
        embedding: List[float], 
        model: str
    ) -> bool:
        """Store task embedding in Milvus."""
        try:
            collection_name = self.collections["task_embeddings"]

            data = [{
                "task_id": task_id,
                "embedding": embedding,
                "model": model,
                "created_at": int(datetime.now().timestamp()),
                "updated_at": int(datetime.now().timestamp())
            }]

            result = self.client.insert(
                collection_name=collection_name,
                data=data
            )

            logger.info("Stored task embedding: task_id=%s", task_id)
            return True

        except Exception as e:
            logger.error("Failed to store task embedding: %s", e)
            return False

    async def search_embedding_cache(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        score_threshold: float = 0.8
    ) -> List[Dict[str, Any]]:
        """Search embedding cache by similarity."""
        try:
            collection_name = self.collections["embedding_cache"]

            search_params = {"metric_type": "COSINE", "params": {"ef": 100}}

            results = self.client.search(
                collection_name=collection_name,
                data=[query_embedding],
                limit=top_k,
                search_params=search_params,
                output_fields=["text_hash", "model", "created_at", "access_count"]
            )

            filtered_results = []
            for hits in results:
                for hit in hits:
                    if hit["distance"] >= score_threshold:
                        filtered_results.append({
                            "text_hash": hit["entity"]["text_hash"],
                            "model": hit["entity"]["model"],
                            "score": hit["distance"],
                            "created_at": hit["entity"]["created_at"],
                            "access_count": hit["entity"]["access_count"]
                        })

            logger.info("Embedding cache search returned %s results", len(filtered_results))
            return filtered_results

        except Exception as e:
            logger.error("Embedding cache search failed: %s", e)
            return []

    async def search_similar_tasks(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        score_threshold: float = 0.7
    ) -> List[Dict[str, Any]]:
        """Search similar tasks by embedding."""
        try:
            collection_name = self.collections["task_embeddings"]

            search_params = {"metric_type": "COSINE", "params": {"ef": 100}}

            results = self.client.search(
                collection_name=collection_name,
                data=[query_embedding],
                limit=top_k,
                search_params=search_params,
                output_fields=["task_id", "model", "created_at"]
            )

            filtered_results = []
            for hits in results:
                for hit in hits:
                    if hit["distance"] >= score_threshold:
                        filtered_results.append({
                            "task_id": hit["entity"]["task_id"],
                            "model": hit["entity"]["model"],
                            "score": hit["distance"],
                            "created_at": hit["entity"]["created_at"]
                        })

            logger.info("Task similarity search returned %s results", len(filtered_results))
            return filtered_results

        except Exception as e:
            logger.error("Task similarity search failed: %s", e)
            return []

    async def get_collection_stats(self) -> Dict[str, Any]:
        """Get Milvus collection statistics."""
        try:
            stats = {}

            for collection_type, collection_name in self.collections.items():
                if self.client.has_collection(collection_name):
                    info = self.client.describe_collection(collection_name)

                    query_result = self.client.query(
                        collection_name=collection_name,
                        expr="id >= 0",
                        output_fields=["count(*)"]
                    )

                    stats[collection_type] = {
                        "collection_name": collection_name,
                        "schema": info,
                        "record_count": len(query_result) if query_result else 0
                    }

            return stats

        except Exception as e:
            logger.error("Failed to get collection statistics: %s", e)
            return {}

    async def close(self):
        """Close Milvus service resources."""
        if self.client:
            logger.info("Milvus service connection closed")


_milvus_service = None

async def get_milvus_service() -> MilvusVectorService:
    """getMilvusservice()"""
    global _milvus_service

    if _milvus_service is None:
        _milvus_service = MilvusVectorService()
        await _milvus_service.initialize()

    return _milvus_service
