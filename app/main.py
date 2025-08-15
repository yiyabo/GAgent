from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Body
from .models import TaskCreate
from .database import init_db
from .scheduler import bfs_schedule
from .executor import execute_task
from .llm import get_default_client
from .services.planning import propose_plan_service, approve_plan_service
from .repository.tasks import default_repo
from .utils import plan_prefix, split_prefix
from contextlib import asynccontextmanager
from .services.context import gather_context


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
    task_id = default_repo.create_task(task.name, status='pending', priority=None)
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
        out.append({"id": rid, "name": nm, "short_name": short, "status": st, "priority": pr})
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
    use_context = bool((payload or {}).get("use_context", False))

    results = []
    if not title:
        # Original behavior: run all pending tasks using scheduler
        for task in bfs_schedule():
            status = execute_task(task, use_context=use_context)
            task_id = task["id"] if isinstance(task, dict) else task[0]
            default_repo.update_task_status(task_id, status)
            results.append({"id": task_id, "status": status})
        return results

    # Filtered by plan title (prefix)
    prefix = plan_prefix(title)
    rows = default_repo.list_tasks_by_prefix(prefix, pending_only=True, ordered=True)

    for task in rows:
        status = execute_task(task, use_context=use_context)
        task_id = task["id"] if isinstance(task, dict) else task[0]
        default_repo.update_task_status(task_id, status)
        results.append({"id": task_id, "status": status})
    return results

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
    include_deps = bool(payload.get("include_deps", True))
    include_plan = bool(payload.get("include_plan", True))
    try:
        k = int(payload.get("k", 5))
    except Exception:
        k = 5
    manual_ids = payload.get("manual")
    if manual_ids and isinstance(manual_ids, list):
        try:
            manual = [int(x) for x in manual_ids]
        except Exception:
            manual = None
    else:
        manual = None
    bundle = gather_context(
        task_id,
        repo=default_repo,
        include_deps=include_deps,
        include_plan=include_plan,
        k=k,
        manual=manual,
    )
    return bundle