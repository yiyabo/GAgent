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
    task_id = default_repo.create_task(
        name=task.name, status="pending", priority=task.priority, parent_id=task.parent_id, task_type=task.task_type
    )
    if task.prompt:
        default_repo.upsert_task_input(task_id, task.prompt)
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

@app.post("/plans/propose")
def propose_plan(payload: Dict[str, Any]):
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

# ... (the rest of the endpoints) ...