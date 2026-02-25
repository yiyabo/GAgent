"""
relatedAPI

Tool Box, analysisexecute. 
"""

from fastapi import APIRouter, HTTPException, Body
from typing import Any, Dict, Optional

from tool_box import get_cache_stats
from tool_box import list_available_tools as _list_available_tools
from tool_box import route_user_request
from tool_box import execute_tool as _execute_tool
from . import register_router

from ..execution.executors.tool_enhanced import execute_task_with_tools
from ..repository.tasks import default_repo
from ..utils.route_helpers import parse_bool, sanitize_context_options

router = APIRouter(tags=["tools"])

register_router(
    namespace="tools",
    version="v1",
    path="/tools",
    router=router,
    tags=["tools"],
    description="Tool Box and bio_tools execution endpoints",
)


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


# Simple web search endpoint for chat CLI
@router.post("/tools/web-search")
async def web_search_api(payload: Dict[str, Any] = Body(...)):
    """Execute a web search via Tool Box.

    Body parameters:
    - query: string (required)
    - max_results: int (optional, default 5)
    - search_engine: string (optional, default 'tavily')
    """
    try:
        query = (payload or {}).get("query", "").strip()
        if not query:
            raise HTTPException(status_code=400, detail="query is required")
        try:
            max_results = int((payload or {}).get("max_results", 5))
        except Exception:
            max_results = 5
        search_engine = (payload or {}).get("search_engine") or "tavily"

        result = await _execute_tool(
            "web_search", query=query, max_results=max_results, search_engine=search_engine
        )

        # Normalize output
        if not isinstance(result, dict):
            return {"query": query, "results": [], "total_results": 0, "engine": search_engine}
        return {
            "query": result.get("query", query),
            "results": result.get("results", []),
            "total_results": result.get("total_results", len(result.get("results", []))),
            "engine": result.get("search_engine", search_engine),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Web search failed: {e}") from e


@router.post("/tools/bio-tools")
async def execute_bio_tools(payload: Dict[str, Any] = Body(...)):
    """
    Execute a registered bioinformatics tool through the tool gateway.

    Required/optional payload fields:
    - tool_name: Tool name (e.g. "seqkit", "blast", "genomad")
    - operation: Operation type (e.g. "stats", "blastn", "end_to_end")
    - input_file: Input file path (optional for some operations)
    - output_file: Output file path (optional)
    - params: Extra tool parameters (optional)
    - timeout: Timeout in seconds (optional, default 3600; <=0 disables execution timeout)
    - background: Run as background job and return immediately (optional)
    - job_id: Optional job id, mainly for operation="job_status"

    Example:
    ```json
    {
        "tool_name": "seqkit",
        "operation": "stats",
        "input_file": "/path/to/file.fasta"
    }
    ```
    """
    try:
        from tool_box import execute_tool

        tool_name = payload.get("tool_name")
        operation = payload.get("operation", "help")
        input_file = payload.get("input_file")
        output_file = payload.get("output_file")
        params = payload.get("params", {})
        timeout = payload.get("timeout", 3600)
        timeout_provided = "timeout" in payload
        background = parse_bool(payload.get("background"), default=False) if "background" in payload else None
        job_id = payload.get("job_id")

        if not tool_name:
            raise HTTPException(status_code=400, detail="tool_name is required")

        kwargs = {
            "tool_name": tool_name,
            "operation": operation,
        }
        if input_file:
            kwargs["input_file"] = input_file
        if output_file:
            kwargs["output_file"] = output_file
        if params:
            kwargs["params"] = params
        if timeout_provided:
            kwargs["timeout"] = timeout
        if background is not None:
            kwargs["background"] = background
        if job_id is not None:
            kwargs["job_id"] = str(job_id)

        result = await execute_tool("bio_tools", **kwargs)

        return {
            "success": result.get("success", False),
            "tool": tool_name,
            "operation": operation,
            "result": result,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"bio_tools execution failed: {str(e)}") from e


@router.get("/tools/bio-tools/jobs/{job_id}")
async def get_bio_tools_job_status(job_id: str):
    """Query background bio_tools job status."""
    try:
        from tool_box import execute_tool

        result = await execute_tool(
            "bio_tools",
            tool_name="job",
            operation="job_status",
            params={"job_id": job_id},
        )
        if not isinstance(result, dict):
            raise HTTPException(status_code=500, detail="Invalid job status payload")
        if not result.get("success"):
            raise HTTPException(status_code=404, detail=result.get("error", "Job not found"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to query bio_tools job: {str(e)}") from e


@router.get("/tools/bio-tools/list")
async def list_bio_tools():
    """
    getavailable
    """
    try:
        from tool_box.bio_tools.bio_tools_handler import get_available_bio_tools

        tools = get_available_bio_tools()

        by_category = {}
        for tool in tools:
            cat = tool["category"]
            by_category.setdefault(cat, []).append({
                "name": tool["name"],
                "description": tool["description"],
                "operations": tool["operations"],
            })

        return {
            "success": True,
            "count": len(tools),
            "tools_by_category": by_category,
            "tools": tools,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list bio_tools: {str(e)}") from e


@router.get("/tasks/{task_id}/tool-requirements")
async def get_task_tool_requirements(task_id: int):
    """Analyze tool requirements for a specific task"""
    try:
        from ..services.planning.tool_aware_decomposition import analyze_task_tool_requirements
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
