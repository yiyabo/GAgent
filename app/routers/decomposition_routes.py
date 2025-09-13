"""
递归分解相关API端点

包含任务分解、计划分解、复杂度评估和分解建议功能。
"""

import asyncio
from datetime import datetime
from fastapi import APIRouter, HTTPException, Body
from typing import Any, Dict

from ..errors import BusinessError, ErrorCode
from ..errors.exceptions import SystemError as CustomSystemError
from ..repository.tasks import default_repo
from ..utils.route_helpers import parse_bool, parse_int, parse_opt_float

router = APIRouter(prefix="/tasks", tags=["decomposition"])


@router.post("/{task_id}/decompose")
def decompose_task_endpoint(task_id: int, payload: Dict[str, Any] = Body(default={})):
    """Decompose a task into subtasks using AI-driven recursive decomposition.

    Body parameters:
    - max_subtasks: Maximum number of subtasks to create (default: 8)
    - force: Force decomposition even if task already has subtasks (default: false)
    - tool_aware: Use tool-aware decomposition (default: true)
    """
    max_subtasks = parse_int(payload.get("max_subtasks", 8), default=8, min_value=2, max_value=20)
    force = parse_bool(payload.get("force"), default=False)
    tool_aware = parse_bool(payload.get("tool_aware"), default=True)

    try:
        if tool_aware:
            # Use tool-aware decomposition
            from ..services.planning.tool_aware_decomposition import decompose_task_with_tool_awareness
            result = asyncio.run(
                decompose_task_with_tool_awareness(
                    task_id=task_id, repo=default_repo, max_subtasks=max_subtasks, force=force
                )
            )
        else:
            # Use standard decomposition
            from ..services.planning.recursive_decomposition import decompose_task
            result = decompose_task(task_id, repo=default_repo, max_subtasks=max_subtasks, force=force)

        if not result.get("success"):
            raise BusinessError(
                message=result.get("error", "Task decomposition failed"),
                error_code=ErrorCode.BUSINESS_RULE_VIOLATION,
                context={"task_id": task_id},
            )

        return result
    except (ValueError, TypeError) as e:
        raise CustomSystemError(
            message="Task decomposition failed due to system error",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            cause=e,
            context={"task_id": task_id},
        ) from e


@router.post("/{task_id}/decompose/tool-aware")
async def decompose_task_tool_aware_endpoint(task_id: int, payload: Dict[str, Any] = Body(default={})):
    """Advanced tool-aware task decomposition with enhanced capabilities

    Body parameters:
    - max_subtasks: Maximum number of subtasks to create (default: 8)
    - force: Force decomposition even if task already has subtasks (default: false)
    """
    max_subtasks = parse_int(payload.get("max_subtasks", 8), default=8, min_value=2, max_value=20)
    force = parse_bool(payload.get("force"), default=False)

    try:
        from ..services.planning.tool_aware_decomposition import decompose_task_with_tool_awareness
        result = await decompose_task_with_tool_awareness(
            task_id=task_id, repo=default_repo, max_subtasks=max_subtasks, force=force
        )

        if not result.get("success"):
            raise BusinessError(
                message=result.get("error", "Tool-aware decomposition failed"),
                error_code=ErrorCode.BUSINESS_RULE_VIOLATION,
                context={"task_id": task_id},
            )

        return result
    except (ValueError, TypeError) as e:
        raise CustomSystemError(
            message="Tool-aware decomposition failed due to system error",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            cause=e,
            context={"task_id": task_id},
        ) from e


@router.get("/{task_id}/complexity")
def evaluate_task_complexity_endpoint(task_id: int):
    """Evaluate the complexity of a task for decomposition planning."""
    try:
        task = default_repo.get_task_info(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        task_name = task.get("name", "")
        task_prompt = default_repo.get_task_input_prompt(task_id) or ""

        from ..services.planning.recursive_decomposition import (
            evaluate_task_complexity,
            determine_task_type,
            should_decompose_task,
            MAX_DECOMPOSITION_DEPTH,
        )
        complexity = evaluate_task_complexity(task_name, task_prompt)
        task_type = determine_task_type(task)
        should_decompose = should_decompose_task(task, default_repo)

        return {
            "task_id": task_id,
            "name": task_name,
            "complexity": complexity,
            "task_type": task_type.value,
            "should_decompose": should_decompose,
            "depth": task.get("depth", 0),
            "max_decomposition_depth": MAX_DECOMPOSITION_DEPTH,
        }
    except (ValueError, TypeError) as e:
        raise CustomSystemError(
            message="Task complexity evaluation failed",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            cause=e,
            context={"task_id": task_id},
        ) from e


@router.post("/{task_id}/decompose/with-evaluation")
def decompose_task_with_evaluation_endpoint(task_id: int, payload: Dict[str, Any] = Body(default={})):
    """Decompose a task with quality evaluation and iterative improvement.

    Body parameters:
    - max_subtasks: Maximum number of subtasks to create (default: 8)
    - force: Force decomposition even if task already has subtasks (default: false)
    - quality_threshold: Minimum quality score required (default: 0.7)
    - max_iterations: Maximum number of decomposition attempts (default: 2)
    """
    max_subtasks = parse_int(payload.get("max_subtasks", 8), default=8, min_value=2, max_value=20)
    force = parse_bool(payload.get("force"), default=False)
    quality_threshold = parse_opt_float(payload.get("quality_threshold"), 0.0, 1.0) or 0.7
    max_iterations = parse_int(payload.get("max_iterations", 2), default=2, min_value=1, max_value=5)

    try:
        from ..services.planning.decomposition_with_evaluation import decompose_task_with_evaluation
        result = decompose_task_with_evaluation(
            task_id=task_id,
            repo=default_repo,
            max_subtasks=max_subtasks,
            force=force,
            quality_threshold=quality_threshold,
            max_iterations=max_iterations,
        )

        if not result.get("success"):
            raise BusinessError(
                message=result.get("error", "Enhanced task decomposition failed"),
                error_code=ErrorCode.BUSINESS_RULE_VIOLATION,
                context={"task_id": task_id},
            )

        return result
    except Exception as e:
        raise CustomSystemError(
            message="Enhanced task decomposition failed due to system error",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            cause=e,
            context={"task_id": task_id},
        ) from e


@router.get("/{task_id}/decomposition/recommendation")
def get_decomposition_recommendation(task_id: int, min_complexity_score: float = 0.6):
    """Get intelligent decomposition recommendation for a task."""
    try:
        task = default_repo.get_task_info(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        from ..services.planning.decomposition_with_evaluation import should_decompose_with_quality_check
        min_complexity = parse_opt_float(min_complexity_score, 0.0, 1.0) or 0.6
        recommendation = should_decompose_with_quality_check(
            task=task, repo=default_repo, min_complexity_score=min_complexity
        )

        return {"task_id": task_id, "recommendation": recommendation, "timestamp": datetime.now().isoformat()}
    except HTTPException:
        raise
    except Exception as e:
        raise CustomSystemError(
            message="Failed to generate decomposition recommendation",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            cause=e,
            context={"task_id": task_id},
        ) from e


# 计划分解端点  
@router.post("/plans/{title}/decompose")
def decompose_plan_endpoint(title: str, payload: Dict[str, Any] = Body(default={})):
    """Recursively decompose all tasks in a plan.

    Body parameters:
    - max_depth: Maximum decomposition depth (default: 3)
    """
    max_depth = parse_int(payload.get("max_depth", 3), default=3, min_value=1, max_value=5)

    try:
        from ..services.planning.recursive_decomposition import recursive_decompose_plan
        result = recursive_decompose_plan(title, repo=default_repo, max_depth=max_depth)

        if not result.get("success"):
            raise BusinessError(
                message=result.get("error", "Plan decomposition failed"),
                error_code=ErrorCode.BUSINESS_RULE_VIOLATION,
                context={"plan_title": title},
            )

        return result
    except Exception as e:
        raise CustomSystemError(
            message="Plan decomposition failed due to system error",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            cause=e,
            context={"plan_title": title},
        ) from e
