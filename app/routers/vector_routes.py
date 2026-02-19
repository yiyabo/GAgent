"""Vector storage and similarity search API routes."""

from typing import Any, Dict, List

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..services.storage.hybrid_vector_storage import get_hybrid_storage
from ..services.storage.milvus_service import get_milvus_service

router = APIRouter(prefix="/vector", tags=["Vector"])


class EmbeddingStoreRequest(BaseModel):
    text_hash: str = Field(..., description="Stable hash for the source text")
    embedding: List[float] = Field(..., description="Embedding vector values")
    model: str = Field(..., description="Embedding model name")
    force_sqlite_only: bool = Field(False, description="Store only in SQLite")


class TaskEmbeddingStoreRequest(BaseModel):
    task_id: int = Field(..., description="Task ID")
    embedding: List[float] = Field(..., description="Embedding vector values")
    model: str = Field(..., description="Embedding model name")


class VectorSearchRequest(BaseModel):
    query_embedding: List[float] = Field(..., description="Query embedding vector")
    top_k: int = Field(10, description="Maximum number of results", ge=1, le=100)
    score_threshold: float = Field(0.7, description="Minimum similarity score", ge=0.0, le=1.0)
    prefer_milvus: bool = Field(True, description="Prefer Milvus over SQLite")


class MigrationModeRequest(BaseModel):
    mode: str = Field(..., description="Migration mode", pattern="^(sqlite_only|hybrid|milvus_only)$")


class VectorSearchResponse(BaseModel):
    results: List[Dict[str, Any]]
    total_count: int
    search_time_ms: float
    engine_used: str


class StorageStatsResponse(BaseModel):
    migration_mode: str
    sqlite: Dict[str, Any]
    milvus: Dict[str, Any]
    total_records: int


class HealthCheckResponse(BaseModel):
    status: str
    milvus_status: str
    sqlite_status: str
    collections: Dict[str, Any]


@router.post("/embedding/store", summary="Store text embedding")
async def store_embedding(request: EmbeddingStoreRequest):
    """Store a text embedding in configured vector storage."""
    try:
        storage = await get_hybrid_storage()

        success = await storage.store_embedding(
            text_hash=request.text_hash,
            embedding=request.embedding,
            model=request.model,
            force_sqlite_only=request.force_sqlite_only,
        )

        if not success:
            raise HTTPException(status_code=500, detail="Failed to store embedding")

        return {
            "success": True,
            "message": "Embedding stored successfully",
            "text_hash": request.text_hash,
            "vector_dim": len(request.embedding),
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to store embedding: {exc}")


@router.post("/task-embedding/store", summary="Store task embedding")
async def store_task_embedding(request: TaskEmbeddingStoreRequest):
    """Store task embedding in Milvus task collection."""
    try:
        milvus_service = await get_milvus_service()

        success = await milvus_service.store_task_embedding(
            task_id=request.task_id,
            embedding=request.embedding,
            model=request.model,
        )

        if not success:
            raise HTTPException(status_code=500, detail="Failed to store task embedding")

        return {
            "success": True,
            "message": "Task embedding stored successfully",
            "task_id": request.task_id,
            "vector_dim": len(request.embedding),
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to store task embedding: {exc}")


@router.post("/search/similar", response_model=VectorSearchResponse, summary="Search similar vectors")
async def search_similar_vectors(request: VectorSearchRequest):
    """Search similar vectors from hybrid storage."""
    try:
        import time

        storage = await get_hybrid_storage()

        start_time = time.time()
        results = await storage.search_similar(
            query_embedding=request.query_embedding,
            top_k=request.top_k,
            score_threshold=request.score_threshold,
            prefer_milvus=request.prefer_milvus,
        )
        search_time = (time.time() - start_time) * 1000

        return VectorSearchResponse(
            results=results,
            total_count=len(results),
            search_time_ms=round(search_time, 2),
            engine_used="Milvus" if request.prefer_milvus else "SQLite",
        )

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Vector search failed: {exc}")


@router.get("/search/tasks/{task_id}/similar", summary="Search similar tasks")
async def search_similar_tasks(
    task_id: int,
    top_k: int = Query(10, description="Maximum number of results", ge=1, le=100),
    score_threshold: float = Query(0.7, description="Minimum similarity score", ge=0.0, le=1.0),
):
    """Placeholder endpoint for task-to-task similarity search."""
    try:
        return {
            "message": "Task-to-task similarity search is not implemented yet",
            "task_id": task_id,
            "parameters": {
                "top_k": top_k,
                "score_threshold": score_threshold,
            },
        }

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Task similarity search failed: {exc}")


@router.get("/stats", response_model=StorageStatsResponse, summary="Get vector storage stats")
async def get_storage_stats():
    """Get hybrid vector storage statistics."""
    try:
        storage = await get_hybrid_storage()
        stats = await storage.get_storage_stats()

        total_records = 0
        if "sqlite" in stats:
            total_records += stats["sqlite"].get("record_count", 0)
        if "milvus" in stats:
            for collection_info in stats["milvus"].values():
                if isinstance(collection_info, dict):
                    total_records += collection_info.get("record_count", 0)

        return StorageStatsResponse(
            migration_mode=stats.get("migration_mode", "unknown"),
            sqlite=stats.get("sqlite", {}),
            milvus=stats.get("milvus", {}),
            total_records=total_records,
        )

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch storage stats: {exc}")


@router.get("/health", response_model=HealthCheckResponse, summary="Check vector storage health")
async def health_check():
    """Run health checks for Milvus and SQLite backends."""
    try:
        milvus_status = "unknown"
        collections: Dict[str, Any] = {}

        try:
            milvus_service = await get_milvus_service()
            collections = await milvus_service.get_collection_stats()
            milvus_status = "healthy"
        except Exception as exc:
            milvus_status = f"error: {exc}"

        sqlite_status = "unknown"
        try:
            storage = await get_hybrid_storage()
            stats = await storage.get_storage_stats()
            if "sqlite" in stats:
                sqlite_status = "healthy"
        except Exception as exc:
            sqlite_status = f"error: {exc}"

        overall_status = "healthy" if (milvus_status == "healthy" or sqlite_status == "healthy") else "unhealthy"

        return HealthCheckResponse(
            status=overall_status,
            milvus_status=milvus_status,
            sqlite_status=sqlite_status,
            collections=collections,
        )

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Vector health check failed: {exc}")


@router.post("/migrate", summary="Migrate SQLite vectors to Milvus")
async def migrate_data():
    """Migrate vector data from SQLite into Milvus."""
    try:
        storage = await get_hybrid_storage()
        result = await storage.migrate_from_sqlite_to_milvus()

        if not result.get("success", False):
            raise HTTPException(status_code=500, detail=f"Migration failed: {result.get('error', 'unknown error')}")

        return {
            "success": True,
            "message": "Migration completed",
            "migration_result": result,
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Migration failed: {exc}")


@router.post("/config/mode", summary="Switch vector storage mode")
async def switch_migration_mode(request: MigrationModeRequest):
    """Switch runtime migration mode for hybrid storage."""
    try:
        await get_hybrid_storage(migration_mode=request.mode)

        return {
            "success": True,
            "message": f"Migration mode switched to {request.mode}",
            "mode": request.mode,
        }

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to switch migration mode: {exc}")


@router.delete("/collections/reset", summary="Reset vector collections")
async def reset_collections():
    """Reset Milvus collections (currently not implemented)."""
    try:
        return {
            "message": "Collection reset is not implemented yet",
            "status": "not_implemented",
        }

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to reset collections: {exc}")


@router.get("/performance/benchmark", summary="Benchmark vector search performance")
async def performance_benchmark(
    test_vectors: int = Query(100, description="Number of benchmark vectors", ge=1, le=1000)
):
    """Benchmark Milvus and SQLite search latency."""
    try:
        import time

        storage = await get_hybrid_storage()

        test_embedding = np.random.rand(1024).tolist()

        start_time = time.time()
        milvus_results = await storage.search_similar(test_embedding, top_k=10, prefer_milvus=True)
        milvus_time = (time.time() - start_time) * 1000

        start_time = time.time()
        sqlite_results = await storage.search_similar(test_embedding, top_k=10, prefer_milvus=False)
        sqlite_time = (time.time() - start_time) * 1000

        speedup = sqlite_time / milvus_time if milvus_time > 0 else 0

        return {
            "benchmark_results": {
                "milvus": {
                    "search_time_ms": round(milvus_time, 2),
                    "results_count": len(milvus_results),
                },
                "sqlite": {
                    "search_time_ms": round(sqlite_time, 2),
                    "results_count": len(sqlite_results),
                },
                "performance_improvement": f"{speedup:.1f}x" if speedup > 0 else "N/A",
            },
            "test_parameters": {
                "vector_dimension": 1024,
                "test_vectors": test_vectors,
            },
        }

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Benchmark failed: {exc}")
