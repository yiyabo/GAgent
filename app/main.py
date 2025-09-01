import os
import json
import logging
from contextlib import asynccontextmanager

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .database import get_db, init_db
from .executor import execute_task
from .llm import get_default_client
from .models import TaskCreate, TaskUpdate
from .repository.tasks import default_repo
from .scheduler import bfs_schedule, postorder_schedule, requires_dag_order
from .services.context import gather_context
from .services.context_budget import apply_budget
from .services.planning import approve_plan_service, propose_plan_service, generate_task_context


def _parse_bool(val, default: bool = False) -> bool:
    if isinstance(val, bool):
        return val
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        v = val.strip().lower()
        if v in {"true", "1", "yes", "on"}:
            return True
        if v in {"false", "0", "no", "off"}:
            return False
    return default


def _parse_int(val, default: int, min_value: int, max_value: int) -> int:
    try:
        i = int(val)
    except Exception:
        return default
    try:
        i = max(min_value, min(int(i), max_value))
    except Exception:
        return default
    return i


def _parse_opt_float(val, min_value: float, max_value: float):
    if val is None:
        return None
    try:
        f = float(val)
    except Exception:
        return None
    try:
        f = max(min_value, min(float(f), max_value))
    except Exception:
        return None
    return f


def _parse_opt_int(val, min_value: int, max_value: int):
    if val is None:
        return None
    try:
        i = int(val)
    except Exception:
        return None
    try:
        i = max(min_value, min(int(i), max_value))
    except Exception:
        return None
    return i


def _parse_strategy(val) -> str:
    if not isinstance(val, str):
        return "truncate"
    v = val.strip().lower()
    return v if v in {"truncate", "sentence"} else "truncate"


def _parse_schedule(val) -> str:
    """Parse scheduling strategy for /run: 'bfs' (default), 'dag', or 'postorder'."""
    if not isinstance(val, str):
        return "bfs"
    v = val.strip().lower()
    return v if v in {"bfs", "dag", "postorder"} else "bfs"


def _sanitize_manual_list(vals) -> Optional[List[int]]:
    if not isinstance(vals, list):
        return None
    out: List[int] = []
    for x in vals:
        try:
            out.append(int(x))
        except Exception:
            continue
    if not out:
        return None
    dedup = list(dict.fromkeys(out))
    return dedup[:50]


def _sanitize_context_options(co: Dict[str, Any]) -> Dict[str, Any]:
    co = co or {}
    return {
        "include_deps": _parse_bool(co.get("include_deps"), default=True),
        "include_plan": _parse_bool(co.get("include_plan"), default=True),
        "k": _parse_int(co.get("k", 5), default=5, min_value=0, max_value=50),
        "manual": _sanitize_manual_list(co.get("manual")),
        "semantic_k": _parse_int(co.get("semantic_k", 5), default=5, min_value=0, max_value=50),
        "min_similarity": _parse_opt_float(co.get("min_similarity", 0.1), min_value=0.0, max_value=1.0) or 0.1,
        "include_ancestors": _parse_bool(co.get("include_ancestors"), default=False),
        "include_siblings": _parse_bool(co.get("include_siblings"), default=False),
        "hierarchy_k": _parse_int(co.get("hierarchy_k", 3), default=3, min_value=0, max_value=20),
        "max_chars": _parse_opt_int(co.get("max_chars"), min_value=0, max_value=100_000),
        "per_section_max": _parse_opt_int(co.get("per_section_max"), min_value=1, max_value=50_000),
        "strategy": _parse_strategy(co.get("strategy")),
        "save_snapshot": _parse_bool(co.get("save_snapshot"), default=False),
        "label": (str(co.get("label")).strip()[:64] if co.get("label") else None),
    }

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(lifespan=lifespan)

try:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
except Exception:
    pass

@app.post("/tasks")
def create_task(task: TaskCreate):
    if not task.name or not task.name.strip():
        raise HTTPException(status_code=400, detail="Task name cannot be empty.")
    
    task_id = default_repo.create_task(
        name=task.name,
        status="pending",
        priority=None, # Default priority is handled by the repository
        task_type=task.task_type,
        parent_id=task.parent_id
    )

    # If a prompt is provided, add it to the task_inputs table
    if task.prompt and task.prompt.strip():
        default_repo.upsert_task_input(task_id, task.prompt)

    # If contexts are provided, add them
    if task.contexts:
        for context in task.contexts:
            if context.label and context.label.strip() and context.content and context.content.strip():
                default_repo.upsert_task_context(
                    task_id=task_id,
                    label=context.label,
                    combined=context.content,
                    sections=[],
                    meta={'source': 'creation'}
                )

    # If a plan_id is provided, link the new task to the plan
    if task.plan_id is not None:
        try:
            default_repo.link_task_to_plan(task.plan_id, task_id)
        except Exception as e:
            raise HTTPException(
                status_code=400, 
                detail=f"Task created with ID {task_id}, but failed to link to plan {task.plan_id}: {e}"
            )

    return {"id": task_id}

@app.get("/tasks")
def list_tasks():
    return default_repo.list_all_tasks()

@app.put("/tasks/{task_id}")
def update_task(task_id: int, task_update: TaskUpdate):
    updated = default_repo.update_task(
        task_id=task_id, name=task_update.name, status=task_update.status, priority=task_update.priority, task_type=task_update.task_type
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"id": task_id, "updated": True}

@app.delete("/tasks/{task_id}")
def delete_task(task_id: int):
    deleted = default_repo.delete_task(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"id": task_id, "deleted": True}

@app.get("/tasks/{task_id}")
def get_task(task_id: int):
    task = default_repo.get_task_info(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.get("/tasks/{task_id}/input")
def get_task_input(task_id: int):
    """Get task input prompt."""
    prompt = default_repo.get_task_input(task_id)
    if prompt is None:
        raise HTTPException(status_code=404, detail="Task input not found")
    return {"id": task_id, "prompt": prompt}


@app.get("/tasks/{task_id}/output")
def get_task_output(task_id: int):
    content = default_repo.get_task_output_content(task_id)
    if content is None:
        raise HTTPException(status_code=404, detail="output not found")
    return {"id": task_id, "content": content}


@app.get("/tasks/{task_id}/context/snapshots")
def list_task_contexts_api(task_id: int):
    snaps = default_repo.list_task_contexts(task_id)
    return {"task_id": task_id, "snapshots": snaps}


@app.get("/tasks/{task_id}/context/snapshots/{label}")
def get_task_context_api(task_id: int, label: str):
    snap = default_repo.get_task_context(task_id, label)
    if not snap:
        raise HTTPException(status_code=404, detail="snapshot not found")
    return snap


@app.put("/tasks/{task_id}/input")
def update_task_input(task_id: int, prompt: Dict[str, str]):
    """Update task input prompt."""
    if "prompt" not in prompt:
        raise HTTPException(status_code=400, detail="prompt field required")
    task = default_repo.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    default_repo.upsert_task_input(task_id, prompt["prompt"])
    return {"id": task_id, "input_updated": True}


@app.put("/tasks/{task_id}/output")
def update_task_output(task_id: int, payload: Dict[str, Any] = Body(...)):
    """Update task output content."""
    if not isinstance(payload, dict) or "content" not in payload:
        raise HTTPException(status_code=400, detail="content field required")
    content = str(payload["content"])
    task = default_repo.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    default_repo.upsert_task_output(task_id, content)
    saved_content = default_repo.get_task_output_content(task_id)
    return {"id": task_id, "output_updated": True, "saved_content": saved_content, "content_matches": content == saved_content}


@app.put("/tasks/{task_id}/context/snapshots/{label}")
def update_task_context_snapshot(task_id: int, label: str, payload: Dict[str, Any] = Body(...)):
    """Update task context snapshot content, sections, or meta."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid payload format")
    fields_to_update = {}
    if "content" in payload:
        fields_to_update["combined"] = str(payload["content"])
    if "sections" in payload:
        if not isinstance(payload["sections"], list):
            raise HTTPException(status_code=400, detail="sections must be a list")
        fields_to_update["sections"] = json.dumps(payload["sections"])
    if "meta" in payload:
        if not isinstance(payload["meta"], dict):
            raise HTTPException(status_code=400, detail="meta must be a dictionary")
        fields_to_update["meta"] = json.dumps(payload["meta"])
    if not fields_to_update:
        raise HTTPException(status_code=400, detail="No fields to update provided. Use 'content', 'sections', or 'meta'.")
    set_clauses = [f"{key} = ?" for key in fields_to_update.keys()]
    params = list(fields_to_update.values()) + [task_id, label]
    with get_db() as conn:
        cursor = conn.execute(
            f"UPDATE task_contexts SET {', '.join(set_clauses)} WHERE task_id = ? AND label = ?",
            params,
        )
        conn.commit()
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"Context snapshot with label '{label}' not found for task {task_id}")
    return {"task_id": task_id, "label": label, "context_updated": True}


@app.post("/tasks/{task_id}/context/snapshots")
def create_task_context_snapshot(task_id: int, payload: Dict[str, Any] = Body(...)):
    """Create a new task context snapshot."""
    if not isinstance(payload, dict) or "label" not in payload or "content" not in payload:
        raise HTTPException(status_code=400, detail="label and content fields required")
    label = str(payload["label"]).strip()
    if not label:
        raise HTTPException(status_code=400, detail="label cannot be empty")
    content = str(payload["content"])
    task = default_repo.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    with get_db() as conn:
        conn.execute(
            "UPDATE task_contexts SET combined = ? WHERE task_id = ? AND label = ?",
            (content, task_id, label),
        )
        inserted = conn.execute(
            "INSERT OR IGNORE INTO task_contexts (task_id, label, combined) VALUES (?, ?, ?)",
            (task_id, label, content),
        ).rowcount
        if inserted:
            conn.commit()
    return {"task_id": task_id, "label": label, "context_created": True}


@app.delete("/tasks/{task_id}/context/snapshots/{label}")
def delete_task_context_snapshot(task_id: int, label: str):
    """Delete a task context snapshot."""
    task = default_repo.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    deleted = 0
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM task_contexts WHERE task_id = ? AND label = ?",
            (task_id, label),
        )
        conn.commit()
        deleted = cursor.rowcount
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Context snapshot not found")
    return {"task_id": task_id, "label": label, "context_deleted": True}


@app.post("/tasks/{task_id}/context/regenerate")
def regenerate_task_context_api(task_id: int):
    """
    Regenerate the initial AI context for a specific task.
    """
    task = default_repo.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    prompt = default_repo.get_task_input(task_id)
    
    task_data_for_generation = {
        "name": task.get("name"),
        "prompt": prompt or "",
        "task_type": task.get("task_type", "composite"),
        "layer": task.get("depth", 0),
        "parent_id": task.get("parent_id")
    }

    llm_client = get_default_client()

    try:
        generate_task_context(
            repo=default_repo,
            client=llm_client,
            task_id=task_id,
            task_data=task_data_for_generation
        )
    except Exception as e:
        logging.error(f"Failed to regenerate context for task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to regenerate context: {e}")

    new_context = default_repo.get_task_context(task_id, label='ai-initial')

    return {
        "success": True,
        "message": "Context regenerated successfully.",
        "context": new_context
    }

@app.post("/plans/propose")
async def propose_plan(payload: Dict[str, Any]):
    try:
        return propose_plan_service(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/plans/approve")
def approve_plan(plan: Dict[str, Any]):
    try:
        return approve_plan_service(plan)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/plans")
def list_plans():
    return {"plans": default_repo.list_plans()}

@app.get("/plans/{plan_id}/tasks")
def get_plan_tasks(plan_id: int):
    rows = default_repo.get_plan_tasks(plan_id)
    return rows


@app.delete("/plans/{plan_id}")
def delete_plan(plan_id: int):
    """Delete an entire plan including all its tasks"""
    try:
        deleted = default_repo.delete_plan(plan_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Plan not found or already deleted")
        return {"plan_id": plan_id, "deleted": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete plan: {str(e)}")

@app.post("/run")
def run_tasks(payload: Optional[Dict[str, Any]] = Body(None)):
    plan_id = (payload or {}).get("plan_id")
    use_context = _parse_bool((payload or {}).get("use_context"), default=True)
    schedule = _parse_schedule((payload or {}).get("schedule", "postorder"))
    rerun_all = _parse_bool((payload or {}).get("rerun_all"), default=False)
    context_options = _sanitize_context_options((payload or {}).get("context_options"))
    enable_evaluation = _parse_bool((payload or {}).get("enable_evaluation"), default=False)
    evaluation_config = (payload or {}).get("evaluation_options")

    results = []
    if plan_id is None:
        plans = default_repo.list_plans()
        for plan in plans:
            results.extend(
                _execute_plan_tasks(
                    plan["id"], schedule, use_context, context_options, enable_evaluation, evaluation_config, rerun_all
                )
            )
    else:
        results.extend(
            _execute_plan_tasks(
                plan_id, schedule, use_context, context_options, enable_evaluation, evaluation_config, rerun_all
            )
        )
    return results

def _execute_plan_tasks(
    plan_id: int,
    schedule: str,
    use_context: bool,
    context_options: Optional[Dict[str, Any]],
    enable_evaluation: bool,
    evaluation_config: Optional[Dict[str, Any]],
    rerun_all: bool = False,
) -> List[Dict[str, Any]]:
    results = []
    try:
        pending_only = not rerun_all
        if schedule == "dag":
            tasks_iter, cycle = requires_dag_order(plan_id, pending_only=pending_only)
            if cycle:
                raise HTTPException(status_code=400, detail=cycle)
        elif schedule == "postorder":
            tasks_iter = postorder_schedule(plan_id, pending_only=pending_only)
        else:
            tasks_iter = bfs_schedule(plan_id, pending_only=pending_only)

        for task in tasks_iter:
            task_id = task["id"]
            default_repo.update_task_status(task_id, "pending")
            default_repo.upsert_task_output(task_id, "")
            if enable_evaluation:
                from .executor import execute_task_with_evaluation
                result = execute_task_with_evaluation(
                    task=task, repo=default_repo, use_context=use_context, context_options=context_options, **(evaluation_config or {})
                )
                status = result.status
            else:
                status = execute_task(task, use_context=use_context, context_options=context_options)
            default_repo.update_task_status(task_id, status)
            results.append({"id": task_id, "status": status})
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error executing plan {plan_id}: {e}")
    return results


@app.post("/tasks/{task_id}/rerun")
def rerun_single_task(task_id: int, payload: Optional[Dict[str, Any]] = Body(None)):
    """
    Re-execute a single task, reset status to pending and clear output

    Body parameters (optional):
    - use_context: Whether to use context (default false)
    - context_options: Context configuration options
    - enable_evaluation: Whether to use evaluation mode (default false)
    - evaluation_options: Evaluation configuration (max_iterations, quality_threshold)
    """
    task = default_repo.get_task_info(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Reset task status
    default_repo.update_task_status(task_id, "pending")

    # Clear task output
    default_repo.upsert_task_output(task_id, "")

    # Parse parameters
    use_context = _parse_bool((payload or {}).get("use_context"), default=False)
    context_options = None
    co = (payload or {}).get("context_options")
    if isinstance(co, dict):
        context_options = _sanitize_context_options(co)

    # Check if evaluation mode is enabled
    enable_evaluation = _parse_bool(
        (payload or {}).get("enable_evaluation"), default=False
    )

    if enable_evaluation:
        # Use enhanced executor with evaluation
        eval_opts = (payload or {}).get("evaluation_options", {})
        max_iterations = _parse_int(
            eval_opts.get("max_iterations", 3), default=3, min_value=1, max_value=10
        )
        quality_threshold = (
            _parse_opt_float(eval_opts.get("quality_threshold"), 0.0, 1.0) or 0.8
        )

        from .executor import execute_task_with_evaluation

        result = execute_task_with_evaluation(
            task=task,
            repo=default_repo,
            max_iterations=max_iterations,
            quality_threshold=quality_threshold,
            use_context=use_context,
            context_options=context_options,
        )

        status = result.status
        default_repo.update_task_status(task_id, status)

        return {
            "task_id": task_id,
            "status": status,
            "rerun_type": "single",
            "evaluation": {
                "score": result.evaluation.overall_score if result.evaluation else None,
                "iterations": result.iterations,
            }
            if enable_evaluation
            else None,
        }
    else:
        # Execute single task with original executor
        status = execute_task(
            task, use_context=use_context, context_options=context_options
        )
        default_repo.update_task_status(task_id, status)

        return {"task_id": task_id, "status": status, "rerun_type": "single"}

# ... (the rest of the endpoints) ...