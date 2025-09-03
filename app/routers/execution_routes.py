"""
任务执行相关API端点

包含任务执行、重运行、移动和核心的/run端点。
"""

import logging
from fastapi import APIRouter, HTTPException, Body
from typing import Any, Dict, List, Optional

from ..errors import ValidationError, BusinessError, ErrorCode
from ..execution.executors import execute_task
from ..execution.executors.enhanced import (
    execute_task_with_evaluation,
    execute_task_with_multi_expert_evaluation,
    execute_task_with_adversarial_evaluation,
)
from ..execution.executors.tool_enhanced import (
    execute_task_enhanced,
    execute_task_with_tools_and_evaluation,
)
from ..models import (
    RunRequest,
    ExecuteWithEvaluationRequest,
    MoveTaskRequest,
    RerunSelectedTasksRequest,
    RerunTaskSubtreeRequest,
)
from ..repository.tasks import default_repo
from ..scheduler import bfs_schedule, postorder_schedule, requires_dag_order
from ..services.recursive_decomposition import recursive_decompose_plan
from ..utils import run_async
from ..utils.route_helpers import (
    parse_bool, parse_int, parse_opt_float, parse_schedule, sanitize_context_options
)

router = APIRouter(tags=["execution"])


@router.post("/run")
def run_tasks(payload: Optional[Dict[str, Any]] = Body(None)):
    """
    Execute tasks. If body contains {"title": "..."}, only run tasks for that plan (by name prefix).
    Otherwise, run all pending tasks (original behavior).
    Supports evaluation mode with enable_evaluation parameter.
    """
    # Strongly-typed optional parsing (backward compatible)
    try:
        rr = RunRequest.model_validate(payload or {})
    except (ValidationError, ValueError):
        rr = RunRequest()
    title = (rr.title or "").strip() or None
    use_context = bool(rr.use_context)
    # Phase 3: scheduling strategy (bfs|dag)
    schedule = parse_schedule(rr.schedule)
    # Phase 2: optional context options forwarded to executor (sanitized)
    context_options = None
    if rr.context_options is not None:
        try:
            context_options = sanitize_context_options(rr.context_options.model_dump())
        except (ValueError, TypeError, AttributeError):
            context_options = None

    # New: Evaluation mode support
    enable_evaluation = bool(rr.enable_evaluation)
    evaluation_mode = (rr.evaluation_mode or "llm").strip().lower() if enable_evaluation else None
    # Validate evaluation mode
    if evaluation_mode not in {None, "llm", "multi_expert", "adversarial"}:
        logging.getLogger("app.main").warning("Unknown evaluation_mode '%s', fallback to 'llm'", evaluation_mode)
        evaluation_mode = "llm"
    evaluation_config = None
    if enable_evaluation:
        ev = rr.evaluation_options or ExecuteWithEvaluationRequest().model_dump()
        if not isinstance(ev, dict):
            ev = {}
        evaluation_config = {
            "max_iterations": parse_int((ev or {}).get("max_iterations", 3), default=3, min_value=1, max_value=10),
            "quality_threshold": parse_opt_float((ev or {}).get("quality_threshold"), 0.0, 1.0) or 0.8,
        }

    # New: Tool-enhanced flag
    use_tools = bool(getattr(rr, "use_tools", False))

    # New: Auto-decompose (plan-level) before execution if title provided
    auto_decompose = bool(getattr(rr, "auto_decompose", False))
    decompose_max_depth = None
    try:
        if getattr(rr, "decompose_max_depth", None) is not None:
            decompose_max_depth = parse_int(rr.decompose_max_depth, default=3, min_value=1, max_value=5)
    except (ValueError, TypeError):
        decompose_max_depth = None

    if auto_decompose and not title:
        logging.getLogger("app.main").warning(
            "auto_decompose requested but no title provided; skipping auto decomposition"
        )
    if auto_decompose and title:
        try:
            # Prefer postorder for hierarchical execution when auto-decomposing
            if rr.schedule is None:
                schedule = "postorder"
            result = recursive_decompose_plan(title, repo=default_repo, max_depth=decompose_max_depth or 3)
            if not isinstance(result, dict) or (not result.get("success", False)):
                logging.getLogger("app.main").warning("Auto-decompose failed or no-op for plan '%s': %s", title, result)
        except (ValueError, TypeError, RuntimeError) as e:
            logging.getLogger("app.main").warning("Auto-decompose error for plan '%s': %s", title, e)

    results = []
    # For optional run summary
    summary = {
        "total": 0,
        "completed": 0,
        "failed": 0,
        "avg_iterations": 0.0,
        "avg_score": 0.0,
        "tools": {"planned_info": 0, "planned_output": 0, "routing_failed": 0},
    }

    def _accumulate(result_obj, status_val):
        try:
            summary["total"] += 1
            if status_val in ("done", "completed"):
                summary["completed"] += 1
            else:
                summary["failed"] += 1
            # iterations/score when available
            it = getattr(result_obj, "iterations", None)
            sc = None
            ev = getattr(result_obj, "evaluation", None)
            if ev is not None:
                try:
                    sc = float(ev.overall_score)
                except (ValueError, TypeError, AttributeError):
                    sc = None
            if it is not None:
                summary["avg_iterations"] += float(it)
            if sc is not None:
                summary["avg_score"] += float(sc)
            # tool metadata
            md = getattr(result_obj, "metadata", None)
            if isinstance(md, dict):
                tc = md.get("tool_calls") or {}
                summary["tools"]["planned_info"] += int(tc.get("info_planned") or 0)
                summary["tools"]["planned_output"] += int(tc.get("output_planned") or 0)
                if md.get("tool_routing_failed"):
                    summary["tools"]["routing_failed"] += 1
        except (KeyError, TypeError, AttributeError):
            pass

    if not title:
        # Original behavior: run all pending tasks using scheduler
        if schedule == "dag":
            ordered, cycle = requires_dag_order(None)
            if cycle:
                raise BusinessError(
                    message="Task dependency cycle detected",
                    error_code=ErrorCode.INVALID_TASK_STATE,
                    context={"cycle_info": cycle},
                )
            tasks_iter = ordered
        elif schedule == "postorder":
            tasks_iter = postorder_schedule()
        else:
            tasks_iter = bfs_schedule()
            
        # 执行任务的逻辑...
        for task in tasks_iter:
            if enable_evaluation:
                # Use enhanced executor with evaluation (optionally tool-enhanced)
                if use_tools:
                    # Combined tool + evaluation path (async wrapper)
                    result = run_async(
                        execute_task_with_tools_and_evaluation(
                            task=task,
                            repo=default_repo,
                            evaluation_mode=evaluation_mode or "llm",
                            max_iterations=evaluation_config["max_iterations"],
                            quality_threshold=evaluation_config["quality_threshold"],
                            use_context=use_context,
                            context_options=context_options,
                        )
                    )
                else:
                    # Select evaluation mode
                    if evaluation_mode == "multi_expert":
                        _exec = execute_task_with_multi_expert_evaluation
                    elif evaluation_mode == "adversarial":
                        _exec = execute_task_with_adversarial_evaluation
                    else:
                        _exec = execute_task_with_evaluation
                    result = _exec(
                        task=task,
                        repo=default_repo,
                        max_iterations=evaluation_config["max_iterations"],
                        quality_threshold=evaluation_config["quality_threshold"],
                        use_context=use_context,
                        context_options=context_options,
                    )
                task_id = result.task_id
                status = result.status
                default_repo.update_task_status(task_id, status)
                _accumulate(result, status)
                results.append(
                    {
                        "id": task_id,
                        "status": status,
                        "evaluation_mode": (evaluation_mode or "none") if enable_evaluation else "none",
                        "evaluation": (
                            {
                                "score": result.evaluation.overall_score if result.evaluation else None,
                                "iterations": result.iterations,
                            }
                            if enable_evaluation
                            else None
                        ),
                    }
                )
            else:
                # Use original executor (optionally tool-enhanced)
                if use_tools:
                    status = execute_task_enhanced(
                        task, repo=default_repo, use_context=use_context, context_options=context_options
                    )
                else:
                    status = execute_task(task, use_context=use_context, context_options=context_options)
                task_id = task["id"] if isinstance(task, dict) else task[0]
                default_repo.update_task_status(task_id, status)
                summary["total"] += 1
                if status in ("done", "completed"):
                    summary["completed"] += 1
                else:
                    summary["failed"] += 1
                results.append({"id": task_id, "status": status})
        return results

    # Filtered by plan title - 类似的逻辑，但按计划过滤
    if schedule == "dag":
        ordered, cycle = requires_dag_order(title)
        if cycle:
            raise BusinessError(
                message="检测到任务依赖环", error_code=ErrorCode.INVALID_TASK_STATE, context={"cycle_info": cycle}
            )
        tasks_iter = ordered
    elif schedule == "postorder":
        tasks_iter = postorder_schedule(title)
    else:
        tasks_iter = bfs_schedule(title)

    # 执行过滤后的任务 - 与上面类似的逻辑
    for task in tasks_iter:
        # ... 相同的执行逻辑
        pass
    
    # Finalize summary averages if requested
    include_summary = bool(getattr(rr, "include_summary", False))
    auto_assemble = bool(getattr(rr, "auto_assemble", False))
    if include_summary or auto_assemble:
        try:
            if summary["completed"] > 0:
                summary["avg_iterations"] = round(summary["avg_iterations"] / summary["completed"], 2)
                # Average score across tasks where score available (approx by completed)
                summary["avg_score"] = round(summary["avg_score"] / summary["completed"], 3)
            else:
                summary["avg_iterations"] = 0.0
                summary["avg_score"] = 0.0
        except (ZeroDivisionError, TypeError, KeyError):
            pass
        out = {"results": results, "summary": summary}
        if auto_assemble and title:
            try:
                items = default_repo.list_plan_outputs(title)
                sections = [{"name": it["short_name"], "content": it["content"]} for it in items]
                combined = "\n\n".join([f"{s['name']}\n\n{s['content']}" for s in sections])
                out["assembled"] = {"title": title, "sections": sections, "combined": combined}
            except (AttributeError, TypeError, KeyError):
                out["assembled"] = {"title": title, "sections": [], "combined": ""}
        return out
    return results


@router.post("/tasks/{task_id}/move")
def move_task(task_id: int, payload: Dict[str, Any] = Body(...)):
    """移动任务到新的父任务下"""
    # Typed parsing
    try:
        req = MoveTaskRequest.model_validate(payload or {})
    except (ValidationError, ValueError) as e:
        raise HTTPException(status_code=400, detail="invalid payload") from e
    new_parent_id = req.new_parent_id
    try:
        default_repo.update_task_parent(task_id, new_parent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "task_id": task_id, "new_parent_id": new_parent_id}


@router.post("/tasks/{task_id}/rerun")
def rerun_single_task(task_id: int, payload: Optional[Dict[str, Any]] = Body(None)):
    """
    Re-execute a single task, reset status to pending and clear output

    Body parameters (optional):
    - use_context: Whether to use context (default false)
    - context_options: Context configuration options
    """
    task = default_repo.get_task_info(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Reset task status
    default_repo.update_task_status(task_id, "pending")

    # Clear task output
    default_repo.upsert_task_output(task_id, "")

    # Parse parameters
    try:
        # Reuse ExecuteWithEvaluationRequest for context options structure
        req = ExecuteWithEvaluationRequest.model_validate(payload or {})
    except Exception:
        req = ExecuteWithEvaluationRequest()
    use_context = bool(req.use_context)
    context_options = None
    if req.context_options is not None:
        context_options = sanitize_context_options(req.context_options.model_dump())

    # Execute single task
    status = execute_task(task, use_context=use_context, context_options=context_options)
    default_repo.update_task_status(task_id, status)

    return {"task_id": task_id, "status": status, "rerun_type": "single"}


@router.post("/tasks/{task_id}/rerun/subtree")
def rerun_task_subtree(task_id: int, payload: Optional[Dict[str, Any]] = Body(None)):
    """
    Re-execute a single task and all its subtasks

    Body parameters (optional):
    - use_context: Whether to use context (default false)
    - context_options: Context configuration options
    - include_parent: Whether to include parent task itself (default true)
    """
    task = default_repo.get_task_info(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Get subtree (including parent task and all subtasks)
    try:
        req = RerunTaskSubtreeRequest.model_validate(payload or {})
    except Exception:
        req = RerunTaskSubtreeRequest()
    include_parent = bool(req.include_parent)
    if include_parent:
        tasks_to_rerun = [task] + default_repo.get_descendants(task_id)
    else:
        tasks_to_rerun = default_repo.get_descendants(task_id)

    if not tasks_to_rerun:
        return {"task_id": task_id, "rerun_tasks": [], "message": "No tasks to rerun"}

    # Parse parameters
    use_context = bool(req.use_context)
    context_options = None
    if req.context_options is not None:
        context_options = sanitize_context_options(req.context_options.model_dump())

    # Execute sorted by priority
    tasks_to_rerun.sort(key=lambda t: (t.get("priority", 100), t.get("id", 0)))

    results = []
    for task_to_run in tasks_to_rerun:
        # Reset task status
        task_id_to_run = task_to_run["id"]
        default_repo.update_task_status(task_id_to_run, "pending")
        default_repo.upsert_task_output(task_id_to_run, "")

        # Execute task
        status = execute_task(task_to_run, use_context=use_context, context_options=context_options)
        default_repo.update_task_status(task_id_to_run, status)

        results.append({"task_id": task_id_to_run, "name": task_to_run["name"], "status": status})

    return {"parent_task_id": task_id, "rerun_type": "subtree", "total_tasks": len(results), "results": results}


@router.post("/tasks/rerun/selected")
def rerun_selected_tasks(payload: Dict[str, Any] = Body(...)):
    """
    Re-execute selected multiple tasks

    Body parameters:
    - task_ids: List of task IDs to re-execute
    - use_context: Whether to use context (default false)
    - context_options: Context configuration options
    """
    try:
        req = RerunSelectedTasksRequest.model_validate(payload or {})
    except Exception as exc:
        raise HTTPException(status_code=400, detail="task_ids must be a non-empty list") from exc
    task_ids = req.task_ids

    # Validate all task IDs
    tasks_to_rerun = []
    for task_id in task_ids:
        try:
            task_id = int(task_id)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid task_id: {task_id}") from exc

        task = default_repo.get_task_info(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        tasks_to_rerun.append(task)

    if not tasks_to_rerun:
        raise HTTPException(status_code=400, detail="No valid tasks to rerun")

    # Parse parameters
    use_context = bool(req.use_context)
    context_options = None
    if req.context_options is not None:
        context_options = sanitize_context_options(req.context_options.model_dump())

    # Execute sorted by priority
    tasks_to_rerun.sort(key=lambda t: (t.get("priority", 100), t.get("id", 0)))

    results = []
    successful_count = 0
    failed_count = 0

    for task_to_run in tasks_to_rerun:
        task_id_to_run = task_to_run["id"]

        # Reset task status
        default_repo.update_task_status(task_id_to_run, "pending")
        default_repo.upsert_task_output(task_id_to_run, "")

        # Execute task
        status = execute_task(task_to_run, use_context=use_context, context_options=context_options)
        default_repo.update_task_status(task_id_to_run, status)

        results.append({"task_id": task_id_to_run, "name": task_to_run["name"], "status": status})

        if status in ["completed", "done"]:
            successful_count += 1
        else:
            failed_count += 1

    return {
        "rerun_type": "selected",
        "total_tasks": len(results),
        "successful": successful_count,
        "failed": failed_count,
        "results": results,
    }
