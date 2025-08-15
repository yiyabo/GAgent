from typing import Any, Dict, List, Optional, Tuple

from ..interfaces import TaskRepository
from ..repository.tasks import default_repo
from ..utils import split_prefix


def _get_task_by_id(task_id: int, repo: TaskRepository) -> Optional[Dict[str, Any]]:
    """Inefficient but simple lookup using list_all_tasks().
    Phase 1 keeps repo surface small; we can add get_task() later if needed.
    """
    try:
        rows = repo.list_all_tasks()
    except Exception:
        return None
    for r in rows:
        try:
            if r.get("id") == task_id:
                return r
        except Exception:
            pass
    return None


def _section_for_task(task: Dict[str, Any], repo: TaskRepository, kind: str) -> Optional[Dict[str, Any]]:
    """Construct a section from a task record using its output if available, else the input prompt.
    Returns None if neither content nor prompt could be found.
    """
    tid = task.get("id") if isinstance(task, dict) else None
    name = task.get("name") if isinstance(task, dict) else None
    if tid is None or name is None:
        return None

    content = repo.get_task_output_content(tid)
    if not content:
        content = repo.get_task_input_prompt(tid) or ""
    if content is None:
        return None

    title, short = split_prefix(name)
    return {
        "task_id": tid,
        "name": name,
        "short_name": short,
        "kind": kind,
        "content": content,
    }


def gather_context(
    task_id: int,
    repo: TaskRepository = default_repo,
    include_deps: bool = True,
    include_plan: bool = True,
    k: int = 5,
    manual: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """Assemble a context bundle for a task.

    - include_deps: include upstream tasks connected via links (requires, refers)
    - include_plan: include sibling tasks in the same plan prefix
    - k: soft cap for number of items taken from each category
    - manual: optional explicit task IDs to include

    Returns a structured dict with sections and a combined string.
    """
    sections: List[Dict[str, Any]] = []

    # 1) Dependencies (requires first, then refers) as provided by repo
    if include_deps:
        try:
            deps = repo.list_dependencies(task_id)
        except Exception:
            deps = []
        # deps already ordered by (kind priority, priority, id)
        for item in deps[:k]:
            sec = _section_for_task(item, repo, kind=f"dep:{item.get('kind','unknown')}")
            if sec:
                sections.append(sec)

    # 2) Siblings in same plan (exclude self)
    if include_plan:
        me = _get_task_by_id(task_id, repo)
        if me and isinstance(me, dict):
            _, short = split_prefix(me.get("name", ""))
            title, _ = split_prefix(me.get("name", ""))
            if title:
                try:
                    siblings = repo.list_plan_tasks(title)
                except Exception:
                    siblings = []
                for s in siblings:
                    sid = s.get("id") if isinstance(s, dict) else None
                    if sid is None or sid == task_id:
                        continue
                    sec = _section_for_task(s, repo, kind="sibling")
                    if sec:
                        sections.append(sec)
                        if len([x for x in sections if x.get("kind") == "sibling"]) >= k:
                            break

    # 3) Manual selections
    if manual:
        # avoid duplicates
        existing_ids = {s["task_id"] for s in sections}
        for mid in manual:
            if mid in existing_ids:
                continue
            mtask = _get_task_by_id(mid, repo)
            if mtask:
                sec = _section_for_task(mtask, repo, kind="manual")
                if sec:
                    sections.append(sec)

    # Build combined text (simple concatenation with headers)
    combined_parts: List[str] = []
    for s in sections:
        header = s.get("short_name") or s.get("name") or f"Task {s.get('task_id')}"
        combined_parts.append(f"## {header}\n\n{s.get('content','')}")
    combined = "\n\n".join(combined_parts)

    return {
        "task_id": task_id,
        "sections": sections,
        "combined": combined,
    }
