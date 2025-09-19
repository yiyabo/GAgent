"""
向量存储API路由
提供完整的向量数据库操作接口
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
import numpy as np

from ..services.storage.hybrid_vector_storage import get_hybrid_storage
from ..services.storage.milvus_service import get_milvus_service

router = APIRouter(prefix="/vector", tags=["向量存储"])

# 请求模型
class EmbeddingStoreRequest(BaseModel):
    text_hash: str = Field(..., description="文本哈希值")
    embedding: List[float] = Field(..., description="向量数据")
    model: str = Field(..., description="模型名称")
    force_sqlite_only: bool = Field(False, description="强制仅使用SQLite")

class TaskEmbeddingStoreRequest(BaseModel):
    task_id: int = Field(..., description="任务ID")
    embedding: List[float] = Field(..., description="向量数据")
    model: str = Field(..., description="模型名称")

class VectorSearchRequest(BaseModel):
    query_embedding: List[float] = Field(..., description="查询向量")
    top_k: int = Field(10, description="返回数量", ge=1, le=100)
    score_threshold: float = Field(0.7, description="相似度阈值", ge=0.0, le=1.0)
    prefer_milvus: bool = Field(True, description="优先使用Milvus")

class MigrationModeRequest(BaseModel):
    mode: str = Field(..., description="迁移模式", pattern="^(sqlite_only|hybrid|milvus_only)$")

# 响应模型
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

@router.post("/embedding/store", summary="存储嵌入向量")
async def store_embedding(request: EmbeddingStoreRequest):
    """存储嵌入向量到混合存储系统"""
    try:
        storage = await get_hybrid_storage()
        
        success = await storage.store_embedding(
            text_hash=request.text_hash,
            embedding=request.embedding,
            model=request.model,
            force_sqlite_only=request.force_sqlite_only
        )
        
        if not success:
            raise HTTPException(status_code=500, detail="向量存储失败")
        
        return {
            "success": True,
            "message": "向量存储成功",
            "text_hash": request.text_hash,
            "vector_dim": len(request.embedding)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"存储失败: {str(e)}")

@router.post("/task-embedding/store", summary="存储任务嵌入向量")
async def store_task_embedding(request: TaskEmbeddingStoreRequest):
    """存储任务嵌入向量"""
    try:
        milvus_service = await get_milvus_service()
        
        success = await milvus_service.store_task_embedding(
            task_id=request.task_id,
            embedding=request.embedding,
            model=request.model
        )
        
        if not success:
            raise HTTPException(status_code=500, detail="任务向量存储失败")
        
        return {
            "success": True,
            "message": "任务向量存储成功",
            "task_id": request.task_id,
            "vector_dim": len(request.embedding)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"任务向量存储失败: {str(e)}")

@router.post("/search/similar", response_model=VectorSearchResponse, summary="搜索相似向量")
async def search_similar_vectors(request: VectorSearchRequest):
    """搜索相似向量"""
    try:
        import time
        storage = await get_hybrid_storage()
        
        start_time = time.time()
        results = await storage.search_similar(
            query_embedding=request.query_embedding,
            top_k=request.top_k,
            score_threshold=request.score_threshold,
            prefer_milvus=request.prefer_milvus
        )
        search_time = (time.time() - start_time) * 1000
        
        return VectorSearchResponse(
            results=results,
            total_count=len(results),
            search_time_ms=round(search_time, 2),
            engine_used="Milvus" if request.prefer_milvus else "SQLite"
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"搜索失败: {str(e)}")

@router.get("/search/tasks/{task_id}/similar", summary="搜索相似任务")
async def search_similar_tasks(
    task_id: int,
    top_k: int = Query(10, description="返回数量", ge=1, le=100),
    score_threshold: float = Query(0.7, description="相似度阈值", ge=0.0, le=1.0)
):
    """根据任务ID搜索相似任务"""
    try:
        # 这里需要先获取任务的向量，然后搜索相似任务
        # 简化实现，实际需要从任务数据库获取向量
        return {
            "message": "相似任务搜索功能开发中",
            "task_id": task_id,
            "parameters": {
                "top_k": top_k,
                "score_threshold": score_threshold
            }
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"相似任务搜索失败: {str(e)}")

@router.get("/stats", response_model=StorageStatsResponse, summary="获取存储统计")
async def get_storage_stats():
    """获取向量存储统计信息"""
    try:
        storage = await get_hybrid_storage()
        stats = await storage.get_storage_stats()
        
        # 计算总记录数
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
            total_records=total_records
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取统计失败: {str(e)}")

@router.get("/health", response_model=HealthCheckResponse, summary="健康检查")
async def health_check():
    """向量存储系统健康检查"""
    try:
        # 检查Milvus状态
        milvus_status = "unknown"
        collections = {}
        
        try:
            milvus_service = await get_milvus_service()
            collections = await milvus_service.get_collection_stats()
            milvus_status = "healthy"
        except Exception as e:
            milvus_status = f"error: {str(e)}"
        
        # 检查SQLite状态
        sqlite_status = "unknown"
        try:
            storage = await get_hybrid_storage()
            stats = await storage.get_storage_stats()
            if "sqlite" in stats:
                sqlite_status = "healthy"
        except Exception as e:
            sqlite_status = f"error: {str(e)}"
        
        # 总体状态
        overall_status = "healthy" if (milvus_status == "healthy" or sqlite_status == "healthy") else "unhealthy"
        
        return HealthCheckResponse(
            status=overall_status,
            milvus_status=milvus_status,
            sqlite_status=sqlite_status,
            collections=collections
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"健康检查失败: {str(e)}")

@router.post("/migrate", summary="执行数据迁移")
async def migrate_data():
    """执行SQLite到Milvus的数据迁移"""
    try:
        storage = await get_hybrid_storage()
        
        # 执行迁移
        result = await storage.migrate_from_sqlite_to_milvus()
        
        if not result.get("success", False):
            raise HTTPException(status_code=500, detail=f"迁移失败: {result.get('error', '未知错误')}")
        
        return {
            "success": True,
            "message": "数据迁移完成",
            "migration_result": result
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"迁移失败: {str(e)}")

@router.post("/config/mode", summary="切换迁移模式")
async def switch_migration_mode(request: MigrationModeRequest):
    """切换向量存储的迁移模式"""
    try:
        # 重新初始化存储服务
        storage = await get_hybrid_storage(migration_mode=request.mode)
        
        return {
            "success": True,
            "message": f"迁移模式已切换到: {request.mode}",
            "mode": request.mode
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"模式切换失败: {str(e)}")

@router.delete("/collections/reset", summary="重置集合")
async def reset_collections():
    """重置Milvus集合 (危险操作)"""
    try:
        # 这是一个危险操作，实际部署时应该添加认证
        return {
            "message": "集合重置功能需要管理员权限",
            "status": "not_implemented"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重置失败: {str(e)}")

@router.get("/performance/benchmark", summary="性能基准测试")
async def performance_benchmark(
    test_vectors: int = Query(100, description="测试向量数量", ge=1, le=1000)
):
    """执行向量存储性能基准测试"""
    try:
        import time
        storage = await get_hybrid_storage()
        
        # 生成测试向量
        test_embedding = np.random.rand(1024).tolist()
        
        # Milvus性能测试
        start_time = time.time()
        milvus_results = await storage.search_similar(
            test_embedding, top_k=10, prefer_milvus=True
        )
        milvus_time = (time.time() - start_time) * 1000
        
        # SQLite性能测试
        start_time = time.time()
        sqlite_results = await storage.search_similar(
            test_embedding, top_k=10, prefer_milvus=False
        )
        sqlite_time = (time.time() - start_time) * 1000
        
        speedup = sqlite_time / milvus_time if milvus_time > 0 else 0
        
        return {
            "benchmark_results": {
                "milvus": {
                    "search_time_ms": round(milvus_time, 2),
                    "results_count": len(milvus_results)
                },
                "sqlite": {
                    "search_time_ms": round(sqlite_time, 2),
                    "results_count": len(sqlite_results)
                },
                "performance_improvement": f"{speedup:.1f}x" if speedup > 0 else "N/A"
            },
            "test_parameters": {
                "vector_dimension": 1024,
                "test_vectors": test_vectors
            }
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"基准测试失败: {str(e)}")
