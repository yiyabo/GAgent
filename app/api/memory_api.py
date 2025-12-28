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
from ..services.memory.memory_service import get_memory_service
from ..services.memory.memory_hooks import get_memory_hooks
from ..services.memory.chat_memory_middleware import get_chat_memory_middleware
from ..routers import register_router

logger = logging.getLogger(__name__)

# Create memory API router
memory_router = APIRouter(prefix="/mcp", tags=["memory"])

register_router(
    namespace="memory",
    version="v1",
    path="/mcp",
    router=memory_router,
    tags=["memory"],
    description="Memory MCP compatible interface",
)


@memory_router.post("/save_memory", response_model=Dict[str, Any])
async def save_memory_endpoint(request: SaveMemoryRequest):
    """
    Save memory to the system

    Compatible with Memory-MCP save_memory interface
    """
    try:
        memory_service = get_memory_service()
        response = await memory_service.save_memory(request)

        # Convert to MCP compatible format
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
        raise HTTPException(status_code=400, detail=f"Data validation error: {str(e)}")
    except Exception as e:
        logger.error(f"Error in save_memory: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@memory_router.post("/query_memory", response_model=Dict[str, Any])
async def query_memory_endpoint(request: QueryMemoryRequest):
    """
    Query memory

    Compatible with Memory-MCP query_memory interface
    """
    try:
        memory_service = get_memory_service()
        response = await memory_service.query_memory(request)

        # Convert to MCP compatible format
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
        raise HTTPException(status_code=400, detail=f"Data validation error: {str(e)}")
    except Exception as e:
        logger.error(f"Error in query_memory: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@memory_router.get("/memory/stats", response_model=MemoryStats)
async def get_memory_stats():
    """Get memory system statistics"""
    try:
        memory_service = get_memory_service()
        return await memory_service.get_memory_stats()
    except Exception as e:
        logger.error(f"Error getting memory stats: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get statistics: {str(e)}")


@memory_router.get("/tools")
async def list_mcp_tools():
    """List MCP tools - Compatible with Memory-MCP interface"""
    return {
        "tools": [
            {
                "name": "save_memory",
                "description": "Save memory to intelligent memory system",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "Memory content"},
                        "memory_type": {
                            "type": "string",
                            "enum": ["conversation", "experience", "knowledge", "context"],
                            "description": "Memory type",
                        },
                        "importance": {
                            "type": "string",
                            "enum": ["critical", "high", "medium", "low", "temporary"],
                            "description": "Importance level",
                        },
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "Tag list"},
                        "related_task_id": {"type": "integer", "description": "Related task ID"},
                    },
                    "required": ["content", "memory_type", "importance"],
                },
            },
            {
                "name": "query_memory",
                "description": "Query related memories from intelligent memory system",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "search_text": {"type": "string", "description": "Search text"},
                        "memory_types": {"type": "array", "items": {"type": "string"}, "description": "Memory type filter"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Return limit"},
                        "min_similarity": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                            "description": "Minimum similarity threshold",
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
    Auto-save task related memory

    Automatically called when task completes, saves task output as memory
    """
    try:
        task_id = task_data.get("task_id")
        task_name = task_data.get("task_name", "")
        task_content = task_data.get("content", "")

        if not task_id or not task_content:
            raise HTTPException(status_code=400, detail="task_id and content are required")

        # Create save request
        save_request = SaveMemoryRequest(
            content=task_content,
            memory_type="experience",  # Task output as experience memory
            importance="medium",
            tags=["task_output", "auto_generated"],
            related_task_id=task_id,
        )

        memory_service = get_memory_service()
        response = await memory_service.save_memory(save_request)

        logger.info(f"Auto-saved task {task_id} as memory {response.memory_id}")

        return {"success": True, "memory_id": response.memory_id, "message": f"Task {task_id} output has been auto-saved as memory"}

    except Exception as e:
        logger.error(f"Failed to auto-save task memory: {e}")
        raise HTTPException(status_code=500, detail=f"Auto-save failed: {str(e)}")


@memory_router.get("/memory/hooks/stats")
async def get_hooks_stats():
    """Get memory hooks statistics"""
    try:
        hooks = get_memory_hooks()
        return hooks.get_stats()
    except Exception as e:
        logger.error(f"Error getting hooks stats: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get hooks statistics: {str(e)}")


@memory_router.post("/memory/hooks/enable")
async def enable_hooks():
    """Enable memory hooks"""
    try:
        hooks = get_memory_hooks()
        hooks.enable()
        return {"success": True, "message": "Memory hooks enabled"}
    except Exception as e:
        logger.error(f"Error enabling hooks: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to enable hooks: {str(e)}")


@memory_router.post("/memory/hooks/disable")
async def disable_hooks():
    """Disable memory hooks"""
    try:
        hooks = get_memory_hooks()
        hooks.disable()
        return {"success": True, "message": "Memory hooks disabled"}
    except Exception as e:
        logger.error(f"Error disabling hooks: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to disable hooks: {str(e)}")


@memory_router.post("/memory/chat/save")
async def save_chat_message(message_data: Dict[str, Any] = Body(...)):
    """
    Save chat message as memory
    
    Intelligently determines message importance and auto-saves
    """
    try:
        content = message_data.get("content", "")
        role = message_data.get("role", "user")
        session_id = message_data.get("session_id")
        force_save = message_data.get("force_save", False)
        
        if not content:
            raise HTTPException(status_code=400, detail="content is required")
        
        middleware = get_chat_memory_middleware()
        memory_id = await middleware.process_message(
            content=content,
            role=role,
            session_id=session_id,
            force_save=force_save,
        )
        
        if memory_id:
            return {
                "success": True,
                "memory_id": memory_id,
                "message": "Message saved as memory"
            }
        else:
            return {
                "success": False,
                "message": "Message did not meet save threshold"
            }
    
    except Exception as e:
        logger.error(f"Failed to save chat message: {e}")
        raise HTTPException(status_code=500, detail=f"Save failed: {str(e)}")
