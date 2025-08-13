import json
import re
import os
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Body
from .models import TaskCreate, Task
from .database import init_db, get_db
from .scheduler import bfs_schedule
from .executor import execute_task, _glm_chat

app = FastAPI()

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

@app.on_event("startup")
def startup():
    init_db()

@app.post("/tasks")
def create_task(task: TaskCreate):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO tasks (name, status) VALUES (?, ?)", (task.name, 'pending'))
        conn.commit()
        return {"id": cur.lastrowid}

@app.get("/tasks")
def list_tasks():
    with get_db() as conn:
        tasks = conn.execute("SELECT id, name, status FROM tasks").fetchall()
        return [dict(t) for t in tasks]

# -------------------------------
# Generic planning endpoints
# -------------------------------
@app.post("/plans/propose")
def propose_plan(payload: Dict[str, Any]):
    """
    Input: { "goal": str, "title"?: str, "sections"?: int, "style"?: str, "notes"?: str }
    Output: { "title": str, "tasks": [ {"name": str, "prompt": str, "priority": int } ] }
    """
    goal = (payload or {}).get("goal") or (payload or {}).get("instruction") or ""
    if not isinstance(goal, str) or not goal.strip():
        raise HTTPException(status_code=400, detail="Missing 'goal' in request body")
    title = (payload or {}).get("title") or goal.strip()[:60]
    sections = (payload or {}).get("sections") or 6
    style = (payload or {}).get("style") or ""
    notes = (payload or {}).get("notes") or ""

    prompt = (
        "You are an expert project planner. Break down the user's goal into a small set of actionable tasks.\n"
        "Return ONLY a JSON object with this schema: {\n"
        "  \"title\": string,\n"
        "  \"tasks\": [ { \"name\": string, \"prompt\": string } ]\n"
        "}\n"
        f"Goal: {goal}\n"
        f"Preferred number of tasks: {sections} (4-8 typical).\n"
        f"Style (optional): {style}\n"
        f"Notes (optional): {notes}\n"
        "Rules: Do not include markdown code fences. Keep concise prompts for each task."
    )

    plan: Dict[str, Any]
    tasks: List[Dict[str, Any]]
    try:
        content = _glm_chat(prompt)
        obj = _parse_json_obj(content) or {}
        if isinstance(obj, list):
            # Interpret a bare list as tasks
            plan = {"title": title, "tasks": obj}
        elif isinstance(obj, dict):
            plan = {"title": obj.get("title") or title, "tasks": obj.get("tasks") or []}
        else:
            plan = {"title": title, "tasks": []}
    except Exception:
        # Fallback minimal plan
        plan = {"title": title, "tasks": []}

    # Normalize tasks and compute priorities
    raw_tasks = plan.get("tasks") or []
    norm_tasks: List[Dict[str, Any]] = []
    for idx, t in enumerate(raw_tasks):
        try:
            name = str(t.get("name") if isinstance(t, dict) else t).strip()
        except Exception:
            name = f"Task {idx+1}"
        if not name:
            name = f"Task {idx+1}"
        default_prompt = (
            f"Fulfill this part of the overall goal.\n"
            f"Overall goal: {goal}\n"
            f"Task: {name}.\n"
            f"Write ~200 words with clear, actionable content."
        )
        prompt_t = t.get("prompt") if isinstance(t, dict) else None
        if not isinstance(prompt_t, str) or not prompt_t.strip():
            prompt_t = default_prompt
        norm_tasks.append({
            "name": name,
            "prompt": prompt_t,
            "priority": (idx + 1) * 10,
        })

    plan_out = {"title": plan.get("title") or title, "tasks": norm_tasks}
    return plan_out


@app.post("/plans/approve")
def approve_plan(plan: Dict[str, Any]):
    """
    Accepts a plan JSON (from /plans/propose), persists tasks into DB as pending.
    To support multiple plans without schema changes, we prefix task names with "[title] ".
    """
    if not isinstance(plan, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")
    title = (plan.get("title") or "Untitled").strip()
    tasks = plan.get("tasks") or []
    if not tasks:
        raise HTTPException(status_code=400, detail="Plan has no tasks to approve")

    prefix = _plan_prefix(title)
    created: List[Dict[str, Any]] = []
    with get_db() as conn:
        cur = conn.cursor()
        for t in tasks:
            name = (t.get("name") or "").strip() if isinstance(t, dict) else str(t)
            if not name:
                continue
            prompt_t = t.get("prompt") if isinstance(t, dict) else None
            if not isinstance(prompt_t, str) or not prompt_t.strip():
                prompt_t = f"Write a focused section for: {name}"
            try:
                priority = int(t.get("priority")) if isinstance(t, dict) and t.get("priority") is not None else None
            except Exception:
                priority = None
            if priority is None:
                priority = (len(created) + 1) * 10

            cur.execute(
                "INSERT INTO tasks (name, status, priority) VALUES (?, ?, ?)",
                (prefix + name, 'pending', priority),
            )
            task_id = cur.lastrowid
            cur.execute(
                "INSERT OR REPLACE INTO task_inputs (task_id, prompt) VALUES (?, ?)",
                (task_id, prompt_t),
            )
            created.append({"id": task_id, "name": name, "priority": priority})
        conn.commit()

    return {"plan": {"title": title}, "created": created}


@app.get("/plans")
def list_plans():
    """Infer existing plan titles by scanning task name prefixes."""
    titles = set()
    with get_db() as conn:
        rows = conn.execute("SELECT name FROM tasks").fetchall()
    for r in rows:
        try:
            nm = r["name"]
        except Exception:
            nm = r[0]
        t, _ = _split_prefix(nm)
        if t:
            titles.add(t)
    return {"plans": sorted(titles)}


@app.get("/plans/{title}/tasks")
def get_plan_tasks(title: str):
    prefix = _plan_prefix(title)
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, name, status, priority
            FROM tasks
            WHERE name LIKE ?
            ORDER BY priority ASC, id ASC
            """,
            (prefix + "%",),
        ).fetchall()
    out = []
    for r in rows:
        try:
            rid, nm, st, pr = r["id"], r["name"], r["status"], r["priority"]
        except Exception:
            rid, nm, st, pr = r[0], r[1], r[2], r[3]
        _, short = _split_prefix(nm)
        out.append({"id": rid, "name": nm, "short_name": short, "status": st, "priority": pr})
    return out


@app.get("/plans/{title}/assembled")
def get_plan_assembled(title: str):
    prefix = _plan_prefix(title)
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT t.name, o.content
            FROM tasks t
            JOIN task_outputs o ON o.task_id = t.id
            WHERE t.name LIKE ?
            ORDER BY t.priority ASC, t.id ASC
            """,
            (prefix + "%",),
        ).fetchall()

    sections = []
    for r in rows:
        try:
            name = r["name"]
            content = r["content"]
        except Exception:
            name, content = r[0], r[1]
        _, short = _split_prefix(name)
        sections.append({"name": short, "content": content})

    combined = "\n\n".join([f"{s['name']}\n\n{s['content']}" for s in sections])
    return {"title": title, "sections": sections, "combined": combined}

@app.get("/health/llm")
def llm_health(ping: bool = False):
    key = os.getenv("GLM_API_KEY")
    url = os.getenv("GLM_API_URL", "https://open.bigmodel.cn/api/paas/v4/chat/completions")
    model = os.getenv("GLM_MODEL", "glm-4-flash")
    info: Dict[str, Any] = {
        "has_api_key": bool(key),
        "url": url,
        "model": model,
    }
    if ping:
        try:
            _ = _glm_chat("ping")
            info["ping_ok"] = True
        except Exception as e:
            info["ping_ok"] = False
            info["error"] = str(e)
    else:
        info["ping_ok"] = None
    return info

# removed legacy endpoint: /reports/protein_binding_site

@app.get("/tasks/{task_id}/output")
def get_task_output(task_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT content FROM task_outputs WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="output not found")
        try:
            content = row["content"]
        except Exception:
            content = row[0]
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
            try:
                task_id = task["id"]
            except Exception:
                task_id = task[0]
            with get_db() as conn:
                conn.execute("UPDATE tasks SET status=? WHERE id=?", (status, task_id))
                conn.commit()
            results.append({"id": task_id, "status": status})
        return results

    # Filtered by plan title (prefix)
    prefix = _plan_prefix(title)
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, name, status, priority
            FROM tasks
            WHERE status='pending' AND name LIKE ?
            ORDER BY priority ASC, id ASC
            """,
            (prefix + "%",),
        ).fetchall()

    for task in rows:
        status = execute_task(task)
        try:
            task_id = task["id"]
        except Exception:
            task_id = task[0]
        with get_db() as conn:
            conn.execute("UPDATE tasks SET status=? WHERE id=?", (status, task_id))
            conn.commit()
        results.append({"id": task_id, "status": status})
    return results