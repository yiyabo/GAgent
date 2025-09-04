from typing import Any, Dict, List, Optional

from ..interfaces import LLMProvider, TaskRepository
from ..llm import get_default_client
from ..prompts import prompt_manager
from ..repository.tasks import default_repo
from ..utils import parse_json_obj, plan_prefix, split_prefix

# parse_json_obj is centralized in app/utils.py


def propose_plan_service(payload: Dict[str, Any], client: Optional[LLMProvider] = None) -> Dict[str, Any]:
    """
    Build a plan via LLM with normalization. Returns { title, tasks }.
    Does not persist anything.
    """
    goal = (payload or {}).get("goal") or (payload or {}).get("instruction") or ""
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("Missing 'goal' in request body")
    provided_title = ((payload or {}).get("title") or "").strip()
    title = provided_title or goal.strip()[:60]
    sections = (payload or {}).get("sections")
    style = (payload or {}).get("style") or ""
    notes = (payload or {}).get("notes") or ""

    # AI automatically determines the number of sections
    if sections is None:
        sections_instruction = (
            "Determine the optimal number of tasks (typically 3-8) based on the complexity and scope of the goal."
        )
    else:
        sections_instruction = f"Preferred number of tasks: {sections} (4-8 typical)."

    # Use centralized English prompt template - escape braces for JSON schema
    prompt = (
        "You are an expert project planner. Break down the user's goal into a small set of actionable tasks.\n"
        "Return ONLY a JSON object with this schema: {{\n"
        '  "title": string,\n'
        '  "tasks": [ {{ "name": string, "prompt": string }} ]\n'
        "}}\n"
        f"Goal: {goal}\n"
        f"{sections_instruction}\n"
        f"Style (optional): {style}\n"
        f"Notes (optional): {notes}\n"
        "Rules: Do not include markdown code fences. Keep concise prompts for each task. Use English only."
    )

    plan: Dict[str, Any]
    client = client or get_default_client()
    try:
        content = client.chat(prompt)
        print(f"DEBUG: LLM response content: {content}")  # Debug output
        obj = parse_json_obj(content) or {}
        print(f"DEBUG: Parsed JSON object: {obj}")  # Debug output
        if isinstance(obj, list):
            plan = {"title": title, "tasks": obj}
        elif isinstance(obj, dict):
            # Canonicalization: if caller provided a title, prefer it over LLM title to avoid plan fragmentation
            llm_title = (obj.get("title") or "").strip()
            final_title = title if provided_title else (llm_title or title)
            plan = {"title": final_title, "tasks": obj.get("tasks") or []}
        else:
            plan = {"title": title, "tasks": []}
    except Exception as e:
        print(f"DEBUG: Exception in plan generation: {e}")  # Debug output
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
            f"Write ~200 words with clear, actionable content in English."
        )
        prompt_t = t.get("prompt") if isinstance(t, dict) else None
        if not isinstance(prompt_t, str) or not prompt_t.strip():
            prompt_t = default_prompt
        norm_tasks.append(
            {
                "name": name,
                "prompt": prompt_t,
                "priority": (idx + 1) * 10,
            }
        )

    return {"title": plan.get("title") or title, "tasks": norm_tasks}


def approve_plan_service(plan: Dict[str, Any], repo: Optional[TaskRepository] = None) -> Dict[str, Any]:
    """
    Persist tasks from plan into DB with name prefixing by [title].
    Optional hierarchical mode: if plan contains {"hierarchical": true}, create a root task
    and attach all tasks as children (parent_id=root_id).
    Returns { plan: { title }, created: [ {id, name, priority} ], (root_id?) }.
    """
    if not isinstance(plan, dict):
        raise ValueError("Body must be a JSON object")
    title = (plan.get("title") or "Untitled").strip()
    tasks = plan.get("tasks") or []
    if not tasks:
        raise ValueError("Plan has no tasks to approve")

    prefix = plan_prefix(title)
    created: List[Dict[str, Any]] = []
    updated: List[Dict[str, Any]] = []
    repo = repo or default_repo

    # Optional hierarchical mode (default False for backward compatibility)
    hierarchical = bool(plan.get("hierarchical"))
    root_id: Optional[int] = None
    if hierarchical:
        root_label = str(plan.get("root_label") or "Plan Root").strip()
        try:
            root_priority = int(plan.get("root_priority")) if plan.get("root_priority") is not None else None
        except Exception:
            root_priority = None
        root_name = f"{prefix}{root_label}"  # e.g., "[Title] Plan Root"
        # Do not pass parent_id for root creation to keep compatibility with repos without parent_id arg
        root_id = repo.create_task(root_name, status="pending", priority=root_priority)
        repo.upsert_task_input(root_id, f"Root task node for plan '{title}'.")

    # Build existing index to avoid duplicate creation under the same plan
    try:
        existing_rows = repo.list_plan_tasks(title)
        existing_by_short: Dict[str, Any] = {}
        for r in existing_rows:
            full_name = r.get("name") if isinstance(r, dict) else r[1]
            # Use split_prefix directly since it's already imported at module level
            _, short = split_prefix(full_name or "")
            existing_by_short[short] = r
    except Exception:
        existing_by_short = {}

    for idx, t in enumerate(tasks):
        name = (t.get("name") or "").strip() if isinstance(t, dict) else str(t)
        if not name:
            continue
        prompt_t = t.get("prompt") if isinstance(t, dict) else None
        if not isinstance(prompt_t, str) or not prompt_t.strip():
            prompt_t = f"Write a focused section for: {name}. Use English only."
        try:
            priority = int(t.get("priority")) if isinstance(t, dict) and t.get("priority") is not None else None
        except Exception:
            priority = None
        if priority is None:
            priority = (len(created) + 1) * 10

        # Dedupe: if a task with the same short name already exists under this plan, update its prompt and priority
        if name in existing_by_short:
            try:
                r = existing_by_short[name]
                task_id = r.get("id") if isinstance(r, dict) else r[0]
                repo.upsert_task_input(task_id, prompt_t)
                # Do not change existing name; optionally update priority
                repo.update_task_status(task_id, r.get("status") or "pending")
                updated.append({"id": task_id, "name": name, "priority": priority})
            except Exception:
                # Fallback to creation if update path fails
                if hierarchical and root_id is not None:
                    task_id = repo.create_task(prefix + name, status="pending", priority=priority, parent_id=root_id)
                else:
                    task_id = repo.create_task(prefix + name, status="pending", priority=priority)
                repo.upsert_task_input(task_id, prompt_t)
                created.append({"id": task_id, "name": name, "priority": priority})
        else:
            # Only pass parent_id when hierarchical mode is enabled to preserve backward compatibility
            if hierarchical and root_id is not None:
                task_id = repo.create_task(prefix + name, status="pending", priority=priority, parent_id=root_id)
            else:
                task_id = repo.create_task(prefix + name, status="pending", priority=priority)
            repo.upsert_task_input(task_id, prompt_t)
            created.append({"id": task_id, "name": name, "priority": priority})

    out = {"plan": {"title": title}, "created": created}
    if updated:
        out["updated"] = updated
    if hierarchical and root_id is not None:
        out["root_id"] = root_id
    return out
