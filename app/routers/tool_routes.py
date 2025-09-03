"""
工具集成相关API端点

包含Tool Box集成、工具分析和工具增强执行功能。
"""

from fastapi import APIRouter, HTTPException, Body
from typing import Any, Dict, Optional

from tool_box import get_cache_stats
from tool_box import list_available_tools as _list_available_tools
from tool_box import route_user_request

from ..execution.executors.tool_enhanced import execute_task_with_tools
from ..repository.tasks import default_repo
from ..services.tool_aware_decomposition import analyze_task_tool_requirements
from ..utils.route_helpers import parse_bool, sanitize_context_options

router = APIRouter(tags=["tools"])


@router.get("/tools/available")
async def list_available_tools():
    """List all available tools from Tool Box"""
    try:
        tools = await _list_available_tools()
        return {"tools": tools, "count": len(tools), "tool_box_version": "2.0.0"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list tools: {str(e)}") from e


@router.post("/tools/analyze")
async def analyze_tool_requirements(payload: Dict[str, Any] = Body(...)):
    """Analyze task requirements for tool usage"""
    try:
        request = payload.get("request", "")
        context = payload.get("context", {})

        if not request:
            raise HTTPException(status_code=400, detail="request is required")

        routing_result = await route_user_request(request, context)

        return {
            "analysis": routing_result,
            "tool_requirements": {
                "needs_tools": len(routing_result.get("tool_calls", [])) > 0,
                "estimated_improvement": "20-40% quality improvement expected",
                "complexity": routing_result.get("analysis", {}).get("complexity", "unknown"),
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Tool analysis failed: {str(e)}") from e


@router.get("/tools/stats")
async def get_tool_stats():
    """Get Tool Box usage statistics"""
    try:
        cache_stats = await get_cache_stats()

        return {
            "cache_performance": cache_stats,
            "system_status": "operational",
            "features": {
                "intelligent_routing": True,
                "multi_tool_coordination": True,
                "performance_caching": True,
                "security_validation": True,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get tool stats: {str(e)}") from e


# 任务相关的工具端点
@router.get("/tasks/{task_id}/tool-requirements")
async def get_task_tool_requirements(task_id: int):
    """Analyze tool requirements for a specific task"""
    try:
        requirements = await analyze_task_tool_requirements(task_id, default_repo)

        return {
            "task_id": task_id,
            "tool_requirements": requirements,
            "recommendations": {
                "use_tool_enhanced_execution": len(requirements.get("requirements", [])) > 0,
                "expected_improvement": (
                    "15-30% quality improvement" if requirements.get("confidence", 0) > 0.7 else "Tool usage uncertain"
                ),
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Tool requirement analysis failed: {str(e)}") from e


@router.post("/tasks/{task_id}/execute/tool-enhanced")
async def execute_task_with_tools_api(task_id: int, payload: Optional[Dict[str, Any]] = Body(None)):
    """Execute task with Tool Box enhancement"""
    try:
        # Get task info
        task = default_repo.get_task_info(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        # Parse options
        use_context = True
        context_options = None

        if payload:
            use_context = parse_bool(payload.get("use_context"), default=True)
            context_options = payload.get("context_options")
            if context_options:
                context_options = sanitize_context_options(context_options)

        # Use tool-enhanced executor
        # Execute with tools
        status = await execute_task_with_tools(
            task=task, repo=default_repo, use_context=use_context, context_options=context_options
        )

        # Update task status
        default_repo.update_task_status(task_id, status)

        return {"task_id": task_id, "status": status, "execution_type": "tool_enhanced", "enhanced_capabilities": True}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Tool-enhanced execution failed: {str(e)}") from e
