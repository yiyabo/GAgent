"""
计划管理相关API端点

包含计划的创建、审批、查询和任务管理功能。
"""

from fastapi import APIRouter
from typing import Any, Dict, List

from ..errors import ValidationError, BusinessError, ErrorCode
from ..repository.tasks import default_repo
from ..services.planning import propose_plan_service, approve_plan_service
from ..utils import split_prefix

router = APIRouter(prefix="/plans", tags=["plans"])


@router.post("/propose")
def propose_plan(payload: Dict[str, Any]):
    """Propose a new plan based on the provided payload.

    Args:
        payload: Dictionary containing plan proposal parameters

    Returns:
        dict: Proposed plan details

    Raises:
        ValidationError: If plan proposal validation fails
    """
    try:
        return propose_plan_service(payload)
    except ValueError as e:
        raise ValidationError(
            message=f"Plan proposal validation failed: {str(e)}",
            error_code=ErrorCode.GOAL_VALIDATION_FAILED,
            cause=e
        ) from e


@router.post("/approve")
def approve_plan(plan: Dict[str, Any]):
    """Approve a proposed plan.

    Args:
        plan: Dictionary containing plan details to approve

    Returns:
        dict: Approved plan details

    Raises:
        BusinessError: If plan approval fails
    """
    try:
        return approve_plan_service(plan)
    except ValueError as e:
        raise BusinessError(
            message=f"Plan approval failed: {str(e)}",
            error_code=ErrorCode.BUSINESS_RULE_VIOLATION,
            cause=e
        ) from e


@router.get("")
def list_plans():
    """List all available plans.

    Returns:
        dict: Dictionary containing list of plan titles
    """
    return {"plans": default_repo.list_plan_titles()}


@router.get("/{title}/tasks")
def get_plan_tasks(title: str):
    """Get all tasks for a specific plan.

    Args:
        title: Plan title/name to retrieve tasks for

    Returns:
        list: List of task dictionaries with id, name, short_name, status,
            priority, task_type, depth, and parent_id
    """
    rows = default_repo.list_plan_tasks(title)
    out: List[Dict[str, Any]] = []
    for r in rows:
        rid, nm, st, pr = r["id"], r["name"], r.get("status"), r.get("priority")
        _, short = split_prefix(nm)
        out.append(
            {
                "id": rid,
                "name": nm,
                "short_name": short,
                "status": st,
                "priority": pr,
                "task_type": r.get("task_type", "atomic"),
                "depth": r.get("depth", 0),
                "parent_id": r.get("parent_id"),
            }
        )
    return out


@router.get("/{title}/assembled")
def get_plan_assembled(title: str):
    """Get assembled content for all tasks in a plan.

    Args:
        title: Plan title/name to retrieve assembled content for

    Returns:
        dict: Dictionary with title, sections, and combined content
    """
    items = default_repo.list_plan_outputs(title)
    sections = [{"name": it["short_name"], "content": it["content"]} for it in items]
    combined = "\n\n".join([f"{s['name']}\n\n{s['content']}" for s in sections])
    return {"title": title, "sections": sections, "combined": combined}
