from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Body
from .models import TaskCreate
from .database import init_db
from .scheduler import bfs_schedule, requires_dag_schedule, requires_dag_order
from .executor import execute_task
from .llm import get_default_client
from .services.planning import propose_plan_service, approve_plan_service
from .repository.tasks import default_repo
from .utils import plan_prefix, split_prefix
from contextlib import asynccontextmanager
from .services.context import gather_context
from .services.context_budget import apply_budget


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
    """Parse scheduling strategy for /run: 'bfs' (default) or 'dag'."""
    if not isinstance(val, str):
        return "bfs"
    v = val.strip().lower()
    return v if v in {"bfs", "dag"} else "bfs"


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
        "tfidf_k": _parse_opt_int(co.get("tfidf_k"), min_value=0, max_value=50),
        # TF-IDF thresholds (optional overrides of env defaults)
        "tfidf_min_score": _parse_opt_float(co.get("tfidf_min_score"), min_value=0.0, max_value=1_000_000.0),
        "tfidf_max_candidates": _parse_opt_int(co.get("tfidf_max_candidates"), min_value=0, max_value=50_000),
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
    else:
        rows = default_repo.list_tasks_by_prefix(prefix, pending_only=True, ordered=True)
        tasks_iter = rows

    for task in tasks_iter:
        status = execute_task(task, use_context=use_context, context_options=context_options)
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
    include_deps = _parse_bool(payload.get("include_deps"), default=True)
    include_plan = _parse_bool(payload.get("include_plan"), default=True)
    k = _parse_int(payload.get("k", 5), default=5, min_value=0, max_value=50)
    # Phase 2 options (optional): budgeting
    max_chars = _parse_opt_int(payload.get("max_chars"), min_value=0, max_value=100_000)
    per_section_max = _parse_opt_int(payload.get("per_section_max"), min_value=1, max_value=50_000)
    # Optional summarization strategy: 'truncate' (default) or 'sentence'
    strategy = _parse_strategy(payload.get("strategy")) if (max_chars is not None or per_section_max is not None) else None
    # Optional TF-IDF retrieved items count
    tfidf_k = _parse_opt_int(payload.get("tfidf_k"), min_value=0, max_value=50)
    # Optional TF-IDF thresholds
    tfidf_min_score = _parse_opt_float(payload.get("tfidf_min_score"), min_value=0.0, max_value=1_000_000.0)
    tfidf_max_candidates = _parse_opt_int(payload.get("tfidf_max_candidates"), min_value=0, max_value=50_000)
    manual = _sanitize_manual_list(payload.get("manual"))
    bundle = gather_context(
        task_id,
        repo=default_repo,
        include_deps=include_deps,
        include_plan=include_plan,
        k=k,
        manual=manual,
        tfidf_k=tfidf_k,
        tfidf_min_score=tfidf_min_score,
        tfidf_max_candidates=tfidf_max_candidates,
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