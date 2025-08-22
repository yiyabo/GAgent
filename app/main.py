from typing import Any, Dict, List, Optional
import os
from fastapi import FastAPI, HTTPException, Body
from .models import TaskCreate
from .database import init_db
from .scheduler import bfs_schedule, requires_dag_schedule, requires_dag_order, postorder_schedule
from .executor import execute_task
from .llm import get_default_client
from .services.planning import propose_plan_service, approve_plan_service
from .repository.tasks import default_repo
from .utils import plan_prefix, split_prefix
from .services.context import gather_context
from .services.context_budget import apply_budget
from .services.index_root import generate_index
# 递归分解功能暂时注释，等待实现
# from .services.planning import (
#     recursive_decompose_task, recursive_decompose_plan, evaluate_task_complexity, should_decompose_task, determine_task_type
# )
from contextlib import asynccontextmanager

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


# GLM semantic retrieval is now the only method


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
    # dedup and cap size
    dedup = list(dict.fromkeys(out))
    return dedup[:50]


def _sanitize_context_options(co: Dict[str, Any]) -> Dict[str, Any]:
    co = co or {}
    return {
        "include_deps": _parse_bool(co.get("include_deps"), default=True),
        "include_plan": _parse_bool(co.get("include_plan"), default=True),
        "k": _parse_int(co.get("k", 5), default=5, min_value=0, max_value=50),
        "manual": _sanitize_manual_list(co.get("manual")),
        # GLM semantic retrieval options (now default enabled)
        "semantic_k": _parse_int(co.get("semantic_k", 5), default=5, min_value=0, max_value=50),
        "min_similarity": _parse_opt_float(co.get("min_similarity", 0.1), min_value=0.0, max_value=1.0) or 0.1,
        # hierarchy options (Phase 5)
        "include_ancestors": _parse_bool(co.get("include_ancestors"), default=False),
        "include_siblings": _parse_bool(co.get("include_siblings"), default=False),
        "hierarchy_k": _parse_int(co.get("hierarchy_k", 3), default=3, min_value=0, max_value=20),
        # budgeting options
        "max_chars": _parse_opt_int(co.get("max_chars"), min_value=0, max_value=100_000),
        # non-positive per_section_max considered invalid → None
        "per_section_max": (
            None
            if (co.get("per_section_max") is not None and _parse_opt_int(co.get("per_section_max"), 1, 50_000) is None)
            else _parse_opt_int(co.get("per_section_max"), min_value=1, max_value=50_000)
        ),
        "strategy": _parse_strategy(co.get("strategy")),
        # snapshot controls
        "save_snapshot": _parse_bool(co.get("save_snapshot"), default=False),
        "label": (str(co.get("label")).strip()[:64] if co.get("label") else None),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(lifespan=lifespan)

# -------------------------------
# Generic plan helpers
# -------------------------------
# helpers centralized in app/utils.py: plan_prefix, split_prefix, parse_json_obj

@app.post("/tasks")
def create_task(task: TaskCreate):
    task_id = default_repo.create_task(task.name, status='pending', priority=None, task_type=task.task_type)
    return {"id": task_id}

@app.get("/tasks")
def list_tasks():
    return default_repo.list_all_tasks()

# -------------------------------
# Generic planning endpoints
# -------------------------------
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
    return {"plans": default_repo.list_plan_titles()}


@app.get("/plans/{title}/tasks")
def get_plan_tasks(title: str):
    rows = default_repo.list_plan_tasks(title)
    out: List[Dict[str, Any]] = []
    for r in rows:
        rid, nm, st, pr = r["id"], r["name"], r.get("status"), r.get("priority")
        _, short = split_prefix(nm)
        out.append({
            "id": rid, 
            "name": nm, 
            "short_name": short, 
            "status": st, 
            "priority": pr,
            "task_type": r.get("task_type", "atomic"),
            "depth": r.get("depth", 0),
            "parent_id": r.get("parent_id")
        })
    return out


@app.get("/plans/{title}/assembled")
def get_plan_assembled(title: str):
    items = default_repo.list_plan_outputs(title)
    sections = [{"name": it["short_name"], "content": it["content"]} for it in items]
    combined = "\n\n".join([f"{s['name']}\n\n{s['content']}" for s in sections])
    return {"title": title, "sections": sections, "combined": combined}

@app.get("/health/llm")
def llm_health(ping: bool = False):
    client = get_default_client()
    info: Dict[str, Any] = client.config()
    if ping:
        info["ping_ok"] = client.ping()
    else:
        info["ping_ok"] = None
    return info

# removed legacy endpoint: /reports/protein_binding_site

@app.get("/tasks/{task_id}/output")
def get_task_output(task_id: int):
    content = default_repo.get_task_output_content(task_id)
    if content is None:
        raise HTTPException(status_code=404, detail="output not found")
    return {"id": task_id, "content": content}

# removed legacy endpoint: /reports/protein_binding_site/assembled

@app.post("/run")
def run_tasks(payload: Optional[Dict[str, Any]] = Body(None)):
    """
    Execute tasks. If body contains {"title": "..."}, only run tasks for that plan (by name prefix).
    Otherwise, run all pending tasks (original behavior).
    """
    title = None
    if isinstance(payload, dict):
        t = payload.get("title")
        if isinstance(t, str) and t.strip():
            title = t.strip()
    use_context = _parse_bool((payload or {}).get("use_context"), default=False)
    # Phase 3: scheduling strategy (bfs|dag)
    schedule = _parse_schedule((payload or {}).get("schedule"))
    # Phase 2: optional context options forwarded to executor (sanitized)
    context_options = None
    co = (payload or {}).get("context_options")
    if isinstance(co, dict):
        context_options = _sanitize_context_options(co)

    results = []
    if not title:
        # Original behavior: run all pending tasks using scheduler
        if schedule == "dag":
            ordered, cycle = requires_dag_order(None)
            if cycle:
                raise HTTPException(status_code=400, detail={"error": "cycle_detected", **cycle})
            tasks_iter = ordered
        elif schedule == "postorder":
            tasks_iter = postorder_schedule()
        else:
            tasks_iter = bfs_schedule()
        for task in tasks_iter:
            status = execute_task(task, use_context=use_context, context_options=context_options)
            task_id = task["id"] if isinstance(task, dict) else task[0]
            default_repo.update_task_status(task_id, status)
            results.append({"id": task_id, "status": status})
        return results

    # Filtered by plan title (prefix)
    prefix = plan_prefix(title)
    if schedule == "dag":
        ordered, cycle = requires_dag_order(title)
        if cycle:
            raise HTTPException(status_code=400, detail={"error": "cycle_detected", **cycle})
        tasks_iter = ordered
    elif schedule == "postorder":
        tasks_iter = postorder_schedule(title)
    else:
        tasks_iter = bfs_schedule(title)

    for task in tasks_iter:
        status = execute_task(task, use_context=use_context, context_options=context_options)
        task_id = task["id"] if isinstance(task, dict) else task[0]
        default_repo.update_task_status(task_id, status)
        results.append({"id": task_id, "status": status})
    return results

# -------------------------------
# Recursive decomposition endpoints (Phase 6)
# -------------------------------

@app.post("/tasks/{task_id}/decompose")
def decompose_task_endpoint(task_id: int, payload: Dict[str, Any] = Body(default={})):
    """Decompose a task into subtasks using AI-driven recursive decomposition.
    
    Body parameters:
    - max_subtasks: Maximum number of subtasks to create (default: 8)
    - force: Force decomposition even if task already has subtasks (default: false)
    """
    max_subtasks = _parse_int(payload.get("max_subtasks", 8), default=8, min_value=2, max_value=20)
    force = _parse_bool(payload.get("force"), default=False)
    
    result = decompose_task(task_id, repo=default_repo, max_subtasks=max_subtasks, force=force)
    
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Decomposition failed"))
    
    return result


@app.post("/plans/{title}/decompose")
def decompose_plan_endpoint(title: str, payload: Dict[str, Any] = Body(default={})):
    """Recursively decompose all tasks in a plan.
    
    Body parameters:
    - max_depth: Maximum decomposition depth (default: 3)
    """
    max_depth = _parse_int(payload.get("max_depth", 3), default=3, min_value=1, max_value=5)
    
    # 递归分解功能暂未实现
    raise HTTPException(status_code=501, detail="Recursive decomposition not yet implemented")


@app.get("/tasks/{task_id}/complexity")
def evaluate_task_complexity_endpoint(task_id: int):
    """Evaluate the complexity of a task for decomposition planning."""
    # 复杂度评估功能暂未实现
    raise HTTPException(status_code=501, detail="Task complexity evaluation not yet implemented")


# -------------------------------
# Global INDEX.md endpoints (Phase 4)
# -------------------------------

def _global_index_path() -> str:
    p = os.environ.get("GLOBAL_INDEX_PATH")
    return p if (isinstance(p, str) and p.strip()) else "INDEX.md"


@app.get("/index")
def get_global_index():
    path = _global_index_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        content = ""
    return {"path": path, "content": content}


@app.put("/index")
def put_global_index(payload: Dict[str, Any] = Body(...)):
    content = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(content, str):
        raise HTTPException(status_code=400, detail="content (string) is required")
    path = payload.get("path") if isinstance(payload, dict) else None
    if not isinstance(path, str) or not path.strip():
        path = _global_index_path()
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"write failed: {e}")
    return {"ok": True, "path": path, "bytes": len(content)}

# -------------------------------
# Context graph endpoints (Phase 1)
# -------------------------------

@app.post("/context/links")
def create_link(payload: Dict[str, Any]):
    try:
        from_id = int(payload.get("from_id"))
        to_id = int(payload.get("to_id"))
        kind = str(payload.get("kind") or "").strip()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid payload")
    if not from_id or not to_id or not kind:
        raise HTTPException(status_code=400, detail="from_id, to_id, kind are required")
    default_repo.create_link(from_id, to_id, kind)
    return {"ok": True, "link": {"from_id": from_id, "to_id": to_id, "kind": kind}}


@app.delete("/context/links")
def delete_link(payload: Dict[str, Any]):
    try:
        from_id = int(payload.get("from_id"))
        to_id = int(payload.get("to_id"))
        kind = str(payload.get("kind") or "").strip()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid payload")
    if not from_id or not to_id or not kind:
        raise HTTPException(status_code=400, detail="from_id, to_id, kind are required")
    default_repo.delete_link(from_id, to_id, kind)
    return {"ok": True}


@app.get("/context/links/{task_id}")
def get_links(task_id: int):
    inbound = default_repo.list_links(to_id=task_id)
    outbound = default_repo.list_links(from_id=task_id)
    return {"task_id": task_id, "inbound": inbound, "outbound": outbound}


@app.post("/tasks/{task_id}/context/preview")
def context_preview(task_id: int, payload: Optional[Dict[str, Any]] = Body(None)):
    payload = payload or {}
    include_deps = _parse_bool(payload.get("include_deps"), default=True)
    include_plan = _parse_bool(payload.get("include_plan"), default=True)
    k = _parse_int(payload.get("k", 5), default=5, min_value=0, max_value=50)
    # Phase 2 options (optional): budgeting
    max_chars = _parse_opt_int(payload.get("max_chars"), min_value=0, max_value=100_000)
    per_section_max = _parse_opt_int(payload.get("per_section_max"), min_value=1, max_value=50_000)
    # Optional summarization strategy: 'truncate' (default) or 'sentence'
    strategy = _parse_strategy(payload.get("strategy")) if (max_chars is not None or per_section_max is not None) else None
    # GLM semantic retrieval options
    semantic_k = _parse_int(payload.get("semantic_k", 5), default=5, min_value=0, max_value=50)
    min_similarity = _parse_opt_float(payload.get("min_similarity"), min_value=0.0, max_value=1.0) or 0.1
    # Hierarchy options (Phase 5)
    include_ancestors = _parse_bool(payload.get("include_ancestors"), default=False)
    include_siblings = _parse_bool(payload.get("include_siblings"), default=False)
    hierarchy_k = _parse_int(payload.get("hierarchy_k", 3), default=3, min_value=0, max_value=20)
    manual = _sanitize_manual_list(payload.get("manual"))
    bundle = gather_context(
        task_id,
        repo=default_repo,
        include_deps=include_deps,
        include_plan=include_plan,
        k=k,
        manual=manual,
        semantic_k=semantic_k,
        min_similarity=min_similarity,
        include_ancestors=include_ancestors,
        include_siblings=include_siblings,
        hierarchy_k=hierarchy_k,
    )
    # Apply budget only when options are provided (backward compatible)
    if (max_chars is not None) or (per_section_max is not None):
        max_chars = _parse_opt_int(max_chars, min_value=0, max_value=100_000)
        per_section_max = _parse_opt_int(per_section_max, min_value=1, max_value=50_000)
        strategy = _parse_strategy(strategy) if strategy else "truncate"
        bundle = apply_budget(bundle, max_chars=max_chars, per_section_max=per_section_max, strategy=strategy)
    return bundle

# -------------------------------
# Context snapshots endpoints (Phase 3)
# -------------------------------

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

# -------------------------------
# Hierarchy endpoints (Phase 5)
# -------------------------------

@app.get("/tasks/{task_id}/children")
def get_task_children(task_id: int):
    children = default_repo.get_children(task_id)
    return {"task_id": task_id, "children": children}


@app.get("/tasks/{task_id}/subtree")
def get_task_subtree(task_id: int):
    subtree = default_repo.get_subtree(task_id)
    if not subtree:
        raise HTTPException(status_code=404, detail="task not found")
    return {"task_id": task_id, "subtree": subtree}


@app.post("/tasks/{task_id}/move")
def move_task(task_id: int, payload: Dict[str, Any] = Body(...)):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="invalid payload")
    new_parent_id_val = payload.get("new_parent_id")
    new_parent_id: Optional[int]
    if new_parent_id_val is None:
        new_parent_id = None
    else:
        try:
            new_parent_id = int(new_parent_id_val)
        except Exception:
            raise HTTPException(status_code=400, detail="new_parent_id must be integer or null")
    try:
        default_repo.update_task_parent(task_id, new_parent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "task_id": task_id, "new_parent_id": new_parent_id}


@app.post("/tasks/{task_id}/rerun")
def rerun_single_task(task_id: int, payload: Optional[Dict[str, Any]] = Body(None)):
    """
    重新执行单个任务，重置状态为pending并清空输出
    
    Body参数（可选）：
    - use_context: 是否使用上下文（默认false）
    - context_options: 上下文配置选项
    """
    task = default_repo.get_task_info(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # 重置任务状态
    default_repo.update_task_status(task_id, "pending")
    
    # 清空任务输出
    default_repo.upsert_task_output(task_id, "")
    
    # 解析参数
    use_context = _parse_bool((payload or {}).get("use_context"), default=False)
    context_options = None
    co = (payload or {}).get("context_options")
    if isinstance(co, dict):
        context_options = _sanitize_context_options(co)
    
    # 执行单个任务
    status = execute_task(task, use_context=use_context, context_options=context_options)
    default_repo.update_task_status(task_id, status)
    
    return {"task_id": task_id, "status": status, "rerun_type": "single"}


@app.post("/tasks/{task_id}/rerun/subtree")
def rerun_task_subtree(task_id: int, payload: Optional[Dict[str, Any]] = Body(None)):
    """
    重新执行单个任务及其所有子任务
    
    Body参数（可选）：
    - use_context: 是否使用上下文（默认false）
    - context_options: 上下文配置选项
    - include_parent: 是否包含父任务本身（默认true）
    """
    task = default_repo.get_task_info(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # 获取子树（包括父任务和所有子任务）
    include_parent = _parse_bool((payload or {}).get("include_parent"), default=True)
    if include_parent:
        tasks_to_rerun = [task] + default_repo.get_descendants(task_id)
    else:
        tasks_to_rerun = default_repo.get_descendants(task_id)
    
    if not tasks_to_rerun:
        return {"task_id": task_id, "rerun_tasks": [], "message": "No tasks to rerun"}
    
    # 解析参数
    use_context = _parse_bool((payload or {}).get("use_context"), default=False)
    context_options = None
    co = (payload or {}).get("context_options")
    if isinstance(co, dict):
        context_options = _sanitize_context_options(co)
    
    # 按优先级排序执行
    tasks_to_rerun.sort(key=lambda t: (t.get("priority", 100), t.get("id", 0)))
    
    results = []
    for task_to_run in tasks_to_rerun:
        # 重置任务状态
        task_id_to_run = task_to_run["id"]
        default_repo.update_task_status(task_id_to_run, "pending")
        default_repo.upsert_task_output(task_id_to_run, "")
        
        # 执行任务
        status = execute_task(task_to_run, use_context=use_context, context_options=context_options)
        default_repo.update_task_status(task_id_to_run, status)
        
        results.append({"task_id": task_id_to_run, "name": task_to_run["name"], "status": status})
    
    return {
        "parent_task_id": task_id,
        "rerun_type": "subtree",
        "total_tasks": len(results),
        "results": results
    }


@app.post("/tasks/rerun/selected")
def rerun_selected_tasks(payload: Dict[str, Any] = Body(...)):
    """
    重新执行选定的多个任务
    
    Body参数：
    - task_ids: 要重新执行的任务ID列表
    - use_context: 是否使用上下文（默认false）
    - context_options: 上下文配置选项
    """
    task_ids = payload.get("task_ids", [])
    if not task_ids or not isinstance(task_ids, list):
        raise HTTPException(status_code=400, detail="task_ids must be a non-empty list")
    
    # 验证所有任务ID
    tasks_to_rerun = []
    for task_id in task_ids:
        try:
            task_id = int(task_id)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail=f"Invalid task_id: {task_id}")
        
        task = default_repo.get_task_info(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
        
        tasks_to_rerun.append(task)
    
    if not tasks_to_rerun:
        raise HTTPException(status_code=400, detail="No valid tasks to rerun")
    
    # 解析参数
    use_context = _parse_bool(payload.get("use_context"), default=False)
    context_options = None
    co = payload.get("context_options")
    if isinstance(co, dict):
        context_options = _sanitize_context_options(co)
    
    # 按优先级排序执行
    tasks_to_rerun.sort(key=lambda t: (t.get("priority", 100), t.get("id", 0)))
    
    results = []
    successful_count = 0
    failed_count = 0
    
    for task_to_run in tasks_to_rerun:
        task_id_to_run = task_to_run["id"]
        
        # 重置任务状态
        default_repo.update_task_status(task_id_to_run, "pending")
        default_repo.upsert_task_output(task_id_to_run, "")
        
        # 执行任务
        status = execute_task(task_to_run, use_context=use_context, context_options=context_options)
        default_repo.update_task_status(task_id_to_run, status)
        
        results.append({
            "task_id": task_id_to_run,
            "name": task_to_run["name"],
            "status": status
        })
        
        if status in ["completed", "done"]:
            successful_count += 1
        else:
            failed_count += 1
    
    return {
        "rerun_type": "selected",
        "total_tasks": len(results),
        "successful": successful_count,
        "failed": failed_count,
        "results": results
    }