import json
from typing import Any, Dict, List, Optional

from ..llm import get_default_client
from ..interfaces import LLMProvider, TaskRepository
from ..repository import tasks as task_repo


def _parse_json_obj(text: str):
    """Try to parse a JSON object or array from arbitrary LLM output."""
    # Extract a JSON-looking block first
    import re

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


def propose_plan_service(payload: Dict[str, Any], client: Optional[LLMProvider] = None) -> Dict[str, Any]:
    """
    Build a plan via LLM with normalization. Returns { title, tasks }.
    Does not persist anything.
    """
    goal = (payload or {}).get("goal") or (payload or {}).get("instruction") or ""
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("Missing 'goal' in request body")
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
    client = client or get_default_client()
    try:
        content = client.chat(prompt)
        obj = _parse_json_obj(content) or {}
        if isinstance(obj, list):
            plan = {"title": title, "tasks": obj}
        elif isinstance(obj, dict):
            plan = {"title": obj.get("title") or title, "tasks": obj.get("tasks") or []}
        else:
            plan = {"title": title, "tasks": []}
    except Exception:
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

    return {"title": plan.get("title") or title, "tasks": norm_tasks}


def approve_plan_service(plan: Dict[str, Any], repo: Optional[TaskRepository] = None) -> Dict[str, Any]:
    """
    Persist tasks from plan into DB with name prefixing by [title].
    Returns { plan: { title }, created: [ {id, name, priority} ] }.
    """
    if not isinstance(plan, dict):
        raise ValueError("Body must be a JSON object")
    title = (plan.get("title") or "Untitled").strip()
    tasks = plan.get("tasks") or []
    if not tasks:
        raise ValueError("Plan has no tasks to approve")

    prefix = f"[{title}] "
    created: List[Dict[str, Any]] = []
    repo = repo or task_repo.default_repo

    for idx, t in enumerate(tasks):
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

        task_id = repo.create_task(prefix + name, status="pending", priority=priority)
        repo.upsert_task_input(task_id, prompt_t)
        created.append({"id": task_id, "name": name, "priority": priority})

    return {"plan": {"title": title}, "created": created}
