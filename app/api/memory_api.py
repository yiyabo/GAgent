"""
Memory MCP API Endpoints

Provides MCP-compatible memory management endpoints integrated with the main system
"""

import logging
from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException

from ..models_memory import (
    MemoryStats,
    QueryMemoryRequest,
    QueryMemoryResponse,
    SaveMemoryRequest,
    SaveMemoryResponse,
)
from ..services.memory_service import get_memory_service

logger = logging.getLogger(__name__)

# 创建记忆API路由器
memory_router = APIRouter(prefix="/mcp", tags=["memory"])


@memory_router.post("/save_memory", response_model=Dict[str, Any])
async def save_memory_endpoint(request: SaveMemoryRequest):
    """
    保存记忆到系统中

    兼容Memory-MCP的save_memory接口
    """
    try:
        memory_service = get_memory_service()
        response = await memory_service.save_memory(request)

        # 转换为MCP兼容格式
        return {
            "context_id": (
                f"{response.task_id}_{response.memory_type.value}" if response.task_id else response.memory_id
            ),
            "task_id": response.task_id,
            "memory_type": response.memory_type.value,
            "content": response.content,
            "created_at": response.created_at.isoformat(),
            "embedding_generated": response.embedding_generated,
            "meta": {
                "importance": request.importance.value,
                "tags": response.tags,
                "agentic_keywords": response.keywords,
                "agentic_context": response.context,
            },
        }

    except ValueError as e:
        logger.warning(f"Validation error in save_memory: {e}")
        raise HTTPException(status_code=400, detail=f"数据验证错误: {str(e)}")
    except Exception as e:
        logger.error(f"Error in save_memory: {e}")
        raise HTTPException(status_code=500, detail=f"内部服务器错误: {str(e)}")


@memory_router.post("/query_memory", response_model=Dict[str, Any])
async def query_memory_endpoint(request: QueryMemoryRequest):
    """
    查询记忆

    兼容Memory-MCP的query_memory接口
    """
    try:
        memory_service = get_memory_service()
        response = await memory_service.query_memory(request)

        # 转换为MCP兼容格式
        memories = []
        for memory in response.memories:
            memories.append(
                {
                    "task_id": memory.task_id,
                    "memory_type": memory.memory_type.value,
                    "content": memory.content,
                    "similarity": memory.similarity,
                    "created_at": memory.created_at.isoformat(),
                    "meta": {
                        "importance": memory.importance.value,
                        "tags": memory.tags,
                        "agentic_keywords": memory.keywords,
                        "agentic_context": memory.context,
                    },
                }
            )

        return {"memories": memories, "total": response.total, "search_time_ms": response.search_time_ms}

    except ValueError as e:
        logger.warning(f"Validation error in query_memory: {e}")
        raise HTTPException(status_code=400, detail=f"数据验证错误: {str(e)}")
    except Exception as e:
        logger.error(f"Error in query_memory: {e}")
        raise HTTPException(status_code=500, detail=f"内部服务器错误: {str(e)}")


@memory_router.get("/memory/stats", response_model=MemoryStats)
async def get_memory_stats():
    """获取记忆系统统计信息"""
    try:
        memory_service = get_memory_service()
        return await memory_service.get_memory_stats()
    except Exception as e:
        logger.error(f"Error getting memory stats: {e}")
        raise HTTPException(status_code=500, detail=f"获取统计信息失败: {str(e)}")


@memory_router.get("/tools")
async def list_mcp_tools():
    """列出MCP工具 - 兼容Memory-MCP接口"""
    return {
        "tools": [
            {
                "name": "save_memory",
                "description": "保存记忆到智能记忆系统",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "记忆内容"},
                        "memory_type": {
                            "type": "string",
                            "enum": ["conversation", "experience", "knowledge", "context"],
                            "description": "记忆类型",
                        },
                        "importance": {
                            "type": "string",
                            "enum": ["critical", "high", "medium", "low", "temporary"],
                            "description": "重要性级别",
                        },
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "标签列表"},
                        "related_task_id": {"type": "integer", "description": "关联任务ID"},
                    },
                    "required": ["content", "memory_type", "importance"],
                },
            },
            {
                "name": "query_memory",
                "description": "从智能记忆系统查询相关记忆",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "search_text": {"type": "string", "description": "搜索文本"},
                        "memory_types": {"type": "array", "items": {"type": "string"}, "description": "记忆类型过滤"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "返回数量限制"},
                        "min_similarity": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                            "description": "最小相似度阈值",
                        },
                    },
                    "required": ["search_text"],
                },
            },
        ]
    }


@memory_router.post("/memory/auto_save_task")
async def auto_save_task_memory(task_data: Dict[str, Any] = Body(...)):
    """
    自动保存任务相关记忆

    当任务完成时自动调用，将任务输出保存为记忆
    """
    try:
        task_id = task_data.get("task_id")
        task_name = task_data.get("task_name", "")
        task_content = task_data.get("content", "")

        if not task_id or not task_content:
            raise HTTPException(status_code=400, detail="task_id和content是必需的")

        # 创建保存请求
        save_request = SaveMemoryRequest(
            content=task_content,
            memory_type="experience",  # 任务输出作为经验记忆
            importance="medium",
            tags=["task_output", "auto_generated"],
            related_task_id=task_id,
        )

        memory_service = get_memory_service()
        response = await memory_service.save_memory(save_request)

        logger.info(f"Auto-saved task {task_id} as memory {response.memory_id}")

        return {"success": True, "memory_id": response.memory_id, "message": f"任务 {task_id} 的输出已自动保存为记忆"}

    except Exception as e:
        logger.error(f"Failed to auto-save task memory: {e}")
        raise HTTPException(status_code=500, detail=f"自动保存失败: {str(e)}")
