import json
import re
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Body
from .models import TaskCreate, Task
from .database import init_db
from .scheduler import bfs_schedule
from .executor import execute_task
from .llm import get_default_client
from .services.planning import propose_plan_service, approve_plan_service
from .repository import tasks as task_repo
from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(lifespan=lifespan)

# -------------------------------
# Generic plan helpers
# -------------------------------
def _plan_prefix(title: str) -> str:
    return f"[{title}] "


def _split_prefix(name: str):
    m = re.match(r"^\[(.*?)\]\s+(.*)$", name)
    if m:
        return m.group(1), m.group(2)
    return None, name


def _parse_json_obj(text: str):
    """Try to parse a JSON object or array from arbitrary LLM output."""
    # Extract a JSON-looking block first
    m = re.search(r"\{.*\}", text, flags=re.S)
    cand = m.group(0) if m else text.strip()
    try:
        obj = json.loads(cand)
        if isinstance(obj, (dict, list)):
            return obj
    except Exception:
        pass
    # Try single->double quotes
    try:
        obj = json.loads(cand.replace("'", '"'))
        if isinstance(obj, (dict, list)):
            return obj
    except Exception:
        pass
    return None

@app.post("/tasks")
def create_task(task: TaskCreate):
    task_id = task_repo.create_task(task.name, status='pending', priority=None)
    return {"id": task_id}

@app.get("/tasks")
def list_tasks():
    return task_repo.list_all_tasks()

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
    return {"plans": task_repo.list_plan_titles()}


@app.get("/plans/{title}/tasks")
def get_plan_tasks(title: str):
    rows = task_repo.list_plan_tasks(title)
    out: List[Dict[str, Any]] = []
    for r in rows:
        rid, nm, st, pr = r["id"], r["name"], r.get("status"), r.get("priority")
        _, short = _split_prefix(nm)
        out.append({"id": rid, "name": nm, "short_name": short, "status": st, "priority": pr})
    return out


@app.get("/plans/{title}/assembled")
def get_plan_assembled(title: str):
    items = task_repo.list_plan_outputs(title)
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
    content = task_repo.get_task_output_content(task_id)
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

    results = []
    if not title:
        # Original behavior: run all pending tasks using scheduler
        for task in bfs_schedule():
            status = execute_task(task)
            task_id = task["id"] if isinstance(task, dict) else task[0]
            task_repo.update_task_status(task_id, status)
            results.append({"id": task_id, "status": status})
        return results

    # Filtered by plan title (prefix)
    prefix = _plan_prefix(title)
    rows = task_repo.list_tasks_by_prefix(prefix, pending_only=True, ordered=True)

    for task in rows:
        status = execute_task(task)
        task_id = task["id"] if isinstance(task, dict) else task[0]
        task_repo.update_task_status(task_id, status)
        results.append({"id": task_id, "status": status})
    return results