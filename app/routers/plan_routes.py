"""
计划管理相关API端点

包含计划的创建、审批、查询和任务管理功能。
"""

import json
from fastapi import APIRouter, HTTPException, Query
from typing import Any, Dict, List, Optional

from ..errors import ValidationError, BusinessError, ErrorCode
from ..repository.tasks import default_repo
from ..services.planning import approve_plan_service
from ..utils import split_prefix
from ..utils.route_helpers import resolve_scope_params

router = APIRouter(prefix="/plans", tags=["plans"])


@router.post("/propose")
def propose_plan(payload: Dict[str, Any]):
    """
    Propose a new plan. Prefer recursive decomposition; fallback to LLM planning if unavailable.
    Accepts: goal (str), optional depth (int)
    """
    goal = payload.get("goal")
    if not goal:
        raise ValidationError(message="Missing 'goal' in request payload", error_code=ErrorCode.GOAL_VALIDATION_FAILED)

    title = f"Plan for '{goal[:50]}...'"

    # First try: recursive decomposition path
    try:
        # Create a root task node
        root_id = default_repo.create_task(name=title, task_type="root")
        default_repo.upsert_task_input(root_id, prompt=goal)

        try:
            from ..services.planning.recursive_decomposition import recursive_decompose_plan
            # Prefer a deeper default to increase chance of actual subtasks
            max_depth = payload.get("depth", 2)
            result = recursive_decompose_plan(title, repo=default_repo, max_depth=max_depth)
            # Regardless of result.success, check if we actually have tasks
            tasks = default_repo.list_plan_tasks(title)
            if isinstance(tasks, list) and len(tasks) > 0:
                return {"title": title, "tasks": tasks}
        except Exception:
            # Fall through to LLM planning fallback
            pass

        # Fallback: LLM-generated plan + approval (hierarchical)
        try:
            from ..services.planning import propose_plan_service, approve_plan_service
            plan = propose_plan_service({"goal": goal, "title": title})
            plan["hierarchical"] = True
            approve_plan_service(plan)
            tasks = default_repo.list_plan_tasks(title)
            if isinstance(tasks, list) and len(tasks) > 0:
                return {"title": title, "tasks": tasks}
            # Final fallback: return an informative error
            raise BusinessError(message="Plan generated but no tasks were created", error_code=ErrorCode.BUSINESS_RULE_VIOLATION)
        except Exception as e:
            raise BusinessError(message=f"Plan generation fallback failed: {e}") from e

    except Exception as e:
        raise BusinessError(
            message=f"Failed to create plan: {str(e)}",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            cause=e,
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
def list_plans(
    session_id: Optional[str] = Query(None, description="仅返回指定会话(session)下的计划"),
    workflow_id: Optional[str] = Query(None, description="仅返回指定工作流(workflow)下的计划"),
):
    """List all available plans.

    Returns:
        dict: Dictionary containing list of plan titles
    """
    resolved_session, resolved_workflow = resolve_scope_params(session_id, workflow_id)
    scoped_tasks = default_repo.list_all_tasks(session_id=resolved_session, workflow_id=resolved_workflow)
    titles = sorted({task["name"] for task in scoped_tasks if task.get("task_type") == "root"})
    return {"plans": titles}


@router.get("/{title}/tasks")
def get_plan_tasks(
    title: str,
    session_id: Optional[str] = Query(None, description="仅返回指定会话(session)下的任务"),
    workflow_id: Optional[str] = Query(None, description="仅返回指定工作流(workflow)下的任务"),
):
    """Get all tasks for a specific plan.

    Args:
        title: Plan title/name to retrieve tasks for

    Returns:
        list: List of task dictionaries with id, name, short_name, status,
            priority, task_type, depth, and parent_id
    """
    resolved_session, resolved_workflow = resolve_scope_params(session_id, workflow_id)
    rows = [
        row
        for row in default_repo.list_plan_tasks(title)
        if (not resolved_session or row.get("session_id") == resolved_session)
        and (not resolved_workflow or row.get("workflow_id") == resolved_workflow)
    ]
    if not rows:
        return []
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


@router.get("/{title}/visualize")
def visualize_plan(
    title: str,
    session_id: Optional[str] = Query(None, description="仅返回指定会话(session)下的计划"),
    workflow_id: Optional[str] = Query(None, description="仅返回指定工作流(workflow)下的计划"),
):
    """Generate a Mermaid.js graph for the plan's task hierarchy."""
    resolved_session, resolved_workflow = resolve_scope_params(session_id, workflow_id)
    tasks = [
        task
        for task in default_repo.list_plan_tasks(title)
        if (not resolved_session or task.get("session_id") == resolved_session)
        and (not resolved_workflow or task.get("workflow_id") == resolved_workflow)
    ]
    if not tasks:
        raise HTTPException(status_code=404, detail="Plan not found or has no tasks.")

    graph_str = "graph TD\n"
    # Create a mapping from task ID to a short, graph-friendly ID
    node_ids = {task["id"]: f"T{task['id']}" for task in tasks}

    for task in tasks:
        task_id = task["id"]
        node_id = node_ids[task_id]
        # Sanitize task name for Mermaid syntax
        task_name = task.get('short_name') or task.get('name', 'Unnamed Task')
        sanitized_name = json.dumps(task_name)
        task_type = task.get('task_type', 'atomic')
        
        # Define node shape based on type
        if task_type == 'root':
            # sanitized_name already includes quotes, so do not wrap with extra quotes
            graph_str += f'    {node_id}[{sanitized_name}]\n'
        elif task_type == 'composite':
            graph_str += f'    {node_id}[/{sanitized_name}/]\n'
        else: # atomic
            graph_str += f'    {node_id}(({sanitized_name}))\n'

        # Add edges from parent to child
        parent_id = task.get("parent_id")
        if parent_id and parent_id in node_ids:
            parent_node_id = node_ids[parent_id]
            graph_str += f"    {parent_node_id} --> {node_id}\n"
            
    return {"title": title, "mermaid_graph": graph_str}


@router.post("/tasks/{task_id}/decompose")
def decompose_specific_task(task_id: int):
    """Decompose a specific composite task into subtasks."""
    from ..services.planning.recursive_decomposition import decompose_task
    result = decompose_task(task_id, repo=default_repo)
    if not result.get("success"):
        raise BusinessError(message=f"Failed to decompose task {task_id}: {result.get('error')}")
    return result


@router.get("/{title}/assembled")
def get_plan_assembled(
    title: str,
    session_id: Optional[str] = Query(None, description="仅返回指定会话(session)下的计划"),
    workflow_id: Optional[str] = Query(None, description="仅返回指定工作流(workflow)下的计划"),
):
    """Get assembled content for all tasks in a plan.

    Args:
        title: Plan title/name to retrieve assembled content for

    Returns:
        dict: Dictionary with title, sections, and combined content
    """
    resolved_session, resolved_workflow = resolve_scope_params(session_id, workflow_id)
    items = [
        item
        for item in default_repo.list_plan_outputs(title)
        if (not resolved_session or item.get("session_id") == resolved_session)
        and (not resolved_workflow or item.get("workflow_id") == resolved_workflow)
    ]
    sections = [{"name": it["short_name"], "content": it["content"]} for it in items]
    combined = "\n\n".join([f"{s['name']}\n\n{s['content']}" for s in sections]) if sections else ""
    return {"title": title, "sections": sections, "combined": combined}
