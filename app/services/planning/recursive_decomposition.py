"""
Task decomposition service.

Decomposes tasks recursively using a ROOT -> COMPOSITE -> ATOMIC structure.
"""

import logging
import os
from enum import Enum
from typing import Any, Dict, List, Optional

from ...interfaces import TaskRepository
from ...repository.tasks import default_repo
from ..planning import propose_plan_service
from ...utils.task_path_generator import get_task_file_path, ensure_task_directory
from ...utils import plan_prefix
from app.services.foundation.settings import get_settings

# Task complexity evaluation thresholds
COMPLEXITY_KEYWORDS = {
    "high": ["system", "", "", "", "", "", "", "", ""],
    "medium": ["", "component", "", "", "", "", "", ""],
    "low": ["", "", "", "", "configuration", "", "update", ""],
}

MAX_DECOMPOSITION_DEPTH = 3  # decompose
MIN_ATOMIC_TASKS = 2  # subtask
MAX_ATOMIC_TASKS = 8  # subtask

_DECOMP_LOGGER = logging.getLogger("app.decomposition")


class TaskType(Enum):
    """Task type in recursive decomposition."""

    ROOT = "root"  # High-level task requiring decomposition.
    COMPOSITE = "composite"  # Mid-level task that can still be decomposed.
    ATOMIC = "atomic"  # Leaf task ready for execution.


def _debug_on() -> bool:
    """Return True when decomposition debug logging is enabled."""
    try:
        s = get_settings()
        return bool(getattr(s, "decomp_debug", False) or getattr(s, "ctx_debug", False))
    except Exception:
        v = os.environ.get("DECOMP_DEBUG") or os.environ.get("CONTEXT_DEBUG")
        return str(v).strip().lower() in {"1", "true", "yes", "on"} if v else False


def _find_root_task(task_id: int, repo: TaskRepository) -> Optional[Dict[str, Any]]:
    """Find root task by walking up parent chain"""
    try:
        current = repo.get_task_info(task_id)
        guard = 0
        while current and guard < 100:
            if current.get("task_type") == "root":
                return current
            parent = repo.get_parent(current.get("id"))
            if not parent:
                break
            current = parent
            guard += 1
    except Exception:
        pass
    return None


def _inject_root_brief_to_prompt(original_prompt: str, task_id: int, repo: TaskRepository) -> str:
    """Inject Root Brief and parent chain into subtask prompt to ensure theme consistency"""
    root_brief = ""
    parent_chain = ""

    try:
        # Get root task
        root = _find_root_task(task_id, repo)
        if root:
            root_name = root.get("name", "")
            root_prompt = repo.get_task_input_prompt(root.get("id")) or ""
            root_brief = f"[ROOT TOPIC] {root_name}\n[CORE OBJECTIVE] {root_prompt[:500]}\n\n"

        # Get parent task
        parent = repo.get_parent(task_id)
        if parent:
            parent_name = parent.get("name", "")
            parent_chain = f"[PARENT TASK] {parent_name}\n\n"
    except Exception as e:
        _DECOMP_LOGGER.warning(f"Failed to inject root brief: {e}")

    # Add explicit theme constraint
    theme_constraint = (
        "\n\nIMPORTANT CONSTRAINT: Keep all content tightly aligned with the ROOT TOPIC above. "
        "Do not drift. If information is insufficient, ask clarifying questions first.\n"
    )

    return f"{root_brief}{parent_chain}{original_prompt}{theme_constraint}"


def evaluate_task_complexity(task_name: str, task_prompt: str = "") -> str:
    """Evaluate task complexity from name and prompt text.

    Args:
        task_name: Task name.
        task_prompt: Optional task prompt/context.

    Returns:
        One of: "high", "medium", "low".
    """
    text = f"{task_name} {task_prompt}".lower()

    high_score = sum(1 for keyword in COMPLEXITY_KEYWORDS["high"] if keyword in text)
    medium_score = sum(1 for keyword in COMPLEXITY_KEYWORDS["medium"] if keyword in text)
    low_score = sum(1 for keyword in COMPLEXITY_KEYWORDS["low"] if keyword in text)

    if high_score >= 2 or (high_score >= 1 and len(text) > 100):
        return "high"
    elif low_score >= 2 or (low_score >= 1 and len(text) < 50):
        return "low"
    else:
        return "medium"


def determine_task_type(task: Dict[str, Any], complexity: str = None) -> TaskType:
    """Determine task type based on task metadata and optional complexity hint.

    Args:
        task: Task dictionary.
        complexity: Optional complexity override.

    Returns:
        Derived TaskType.
    """
    depth = task.get("depth", 0)

    if complexity is not None:
        if depth == 0:
            if complexity == "high":
                return TaskType.ROOT
            elif complexity == "medium":
                return TaskType.COMPOSITE
            else:
                return TaskType.ATOMIC
        elif depth == 1:
            return TaskType.COMPOSITE
        else:
            return TaskType.ATOMIC

    existing_type = task.get("task_type", "atomic")
    if existing_type in ["root", "composite", "atomic"]:
        return TaskType(existing_type)

    if depth == 0:
        if not complexity:
            task_name = task.get("name", "")
            task_prompt = ""  # Repository prompt is optional for classification.
            complexity = evaluate_task_complexity(task_name, task_prompt)

        if complexity == "high":
            return TaskType.ROOT
        elif complexity == "medium":
            return TaskType.COMPOSITE
        else:
            return TaskType.ATOMIC
    elif depth == 1:
        return TaskType.COMPOSITE
    else:
        return TaskType.ATOMIC


def should_decompose_task(task: Dict[str, Any], repo: TaskRepository = None) -> bool:
    """Return whether the task should be decomposed.

    Args:
        task: Task dictionary.
        repo: Optional repository instance.

    Returns:
        True if decomposition is recommended.
    """
    if repo is None:
        repo = default_repo

    task_id = task.get("id")
    depth = task.get("depth", 0)
    task_type = determine_task_type(task)

    if depth >= MAX_DECOMPOSITION_DEPTH - 1:
        return False

    if task_type == TaskType.ATOMIC:
        return False

    try:
        children = repo.get_children(task_id)
        if children:
            pending_children = [c for c in children if c.get("status") == "pending"]
            if len(pending_children) >= MIN_ATOMIC_TASKS:
                return False  # Sufficient pending children already exist.
    except Exception as e:
        if _debug_on():
            _DECOMP_LOGGER.debug({"event": "should_decompose_task.get_children_error", "error": str(e)})
        pass

    return task_type in [TaskType.ROOT, TaskType.COMPOSITE]


def decompose_task(
    task_id: int, repo: TaskRepository = None, max_subtasks: int = MAX_ATOMIC_TASKS, force: bool = False
) -> Dict[str, Any]:
    """Decompose a task into child tasks.

    Args:
        task_id: Target task ID.
        repo: Optional repository instance.
        max_subtasks: Maximum number of subtasks to generate.
        force: Force decomposition even if checks say it is unnecessary.

    Returns:
        Decomposition result payload.
    """
    if repo is None:
        repo = default_repo

    task = repo.get_task_info(task_id)
    if not task:
        return {"success": False, "error": "Task not found"}

    if not force and not should_decompose_task(task, repo):
        return {"success": False, "error": "Task does not need decomposition"}

    task_name = task.get("name", "")
    task_type = determine_task_type(task)

    if _debug_on():
        _DECOMP_LOGGER.debug(
            {
                "event": "decompose_task.start",
                "task_id": task_id,
                "task_name": task_name,
                "task_type": task_type.value,
                "depth": task.get("depth", 0),
            }
        )

    try:
        task_prompt = repo.get_task_input_prompt(task_id) or ""

        decomp_prompt = _build_decomposition_prompt(task_name, task_prompt, task_type, max_subtasks)

        plan_payload = {"goal": decomp_prompt, "title": f"decomposition_{task_name}", "sections": max_subtasks}
        plan_result = propose_plan_service(plan_payload)

        if not isinstance(plan_result, dict) or not plan_result.get("tasks"):
            return {"success": False, "error": "Failed to generate subtasks"}

        subtasks = plan_result.get("tasks", [])
        if not subtasks:
            return {"success": False, "error": "No subtasks generated"}

        created_subtasks = []
        for i, subtask in enumerate(subtasks[:max_subtasks]):
            subtask_name = subtask.get("name", f"Subtask {i+1}")
            subtask_priority = subtask.get("priority", 100 + i * 10)

            parent_depth = task.get("depth", 0)
            if task_type == TaskType.ROOT:
                child_type = TaskType.COMPOSITE.value
            elif parent_depth >= MAX_DECOMPOSITION_DEPTH - 2:
                child_type = TaskType.ATOMIC.value
            else:
                child_type = TaskType.COMPOSITE.value

            # Add plan prefix to subtask names for plan-level grouping.
            from ...utils import split_prefix
            plan_title, _ = split_prefix(task_name)
            if not plan_title and task.get("depth", 0) == 0:
                plan_title = task_name  # Root task name is used as plan title.

            prefix = plan_prefix(plan_title) if plan_title else ""

            subtask_id = repo.create_task(
                name=prefix + subtask_name, status="pending", priority=subtask_priority, parent_id=task_id, task_type=child_type
            )

            subtask_prompt = subtask.get("prompt", "")
            if subtask_prompt:
                # Inject root brief and parent chain to keep theme consistency.
                enhanced_prompt = _inject_root_brief_to_prompt(subtask_prompt, task_id, repo)
                repo.upsert_task_input(subtask_id, enhanced_prompt)

            # Initialize result placeholders for COMPOSITE/ATOMIC tasks.
            try:
                child_info = repo.get_task_info(subtask_id)
                child_path = get_task_file_path(child_info, repo)
                # COMPOSITE -> directory + summary.md
                if child_info.get("task_type") == "composite":
                    if ensure_task_directory(child_path):
                        summary_md = os.path.join(child_path, "summary.md")
                        if not os.path.exists(summary_md):
                            with open(summary_md, "w", encoding="utf-8") as f:
                                f.write(
                                    f"# {subtask_name} - Stage Summary\n\n"
                                    "This document aggregates outputs from all ATOMIC tasks under this COMPOSITE task.\n"
                                )
                # ATOMIC -> file placeholder
                elif child_info.get("task_type") == "atomic":
                    ensure_task_directory(child_path)
                    if not os.path.exists(child_path):
                        with open(child_path, "w", encoding="utf-8") as f:
                            f.write(f"# {subtask_name}\n\n(Auto-generated task document. Execution output will be written here.)\n")
            except Exception as e:
                _DECOMP_LOGGER.warning({
                    "event": "decompose_task.files_init_failed",
                    "task_id": task_id,
                    "child_id": subtask_id,
                    "error": str(e)
                })

            created_subtasks.append(
                {
                    "id": subtask_id,
                    "name": subtask_name,
                    "type": child_type,
                    "task_type": child_type,
                    "priority": subtask_priority,
                }
            )

        if task.get("task_type") == "atomic":
            repo.update_task_type(task_id, task_type.value)

        if _debug_on():
            _DECOMP_LOGGER.debug(
                {"event": "decompose_task.success", "task_id": task_id, "subtasks_created": len(created_subtasks)}
            )

        return {
            "success": True,
            "task_id": task_id,
            "subtasks": created_subtasks,
            "decomposition_depth": task.get("depth", 0) + 1,
        }

    except Exception as e:
        if _debug_on():
            _DECOMP_LOGGER.error({"event": "decompose_task.error", "task_id": task_id, "error": str(e)})
        return {"success": False, "error": str(e)}


def _build_decomposition_prompt(task_name: str, task_prompt: str, task_type: TaskType, max_subtasks: int) -> str:
    """Build decomposition prompt text for ROOT/COMPOSITE tasks.

    Args:
        task_name: Task name.
        task_prompt: Task prompt/context.
        task_type: Current task type.
        max_subtasks: Maximum number of subtasks requested.

    Returns:
        Prompt text for the planner.
    """
    # Use prompt manager for internationalized prompts
    from app.prompts import prompt_manager

    if task_type == TaskType.ROOT:
        intro = prompt_manager.get("decomposition.root_task.intro", 
                                   min_tasks=MIN_ATOMIC_TASKS, 
                                   max_tasks=max_subtasks)
        task_name_label = prompt_manager.get("decomposition.root_task.task_name")
        task_desc_label = prompt_manager.get("decomposition.root_task.task_description")
        principles_label = prompt_manager.get("decomposition.root_task.principles")
        principles_list = prompt_manager.get_category("decomposition")["root_task"]["principles_list"]
        format_instruction = prompt_manager.get("decomposition.root_task.format_instruction")

        principles_text = "\n".join(principles_list)

        decomp_instruction = f"""
{intro}

{task_name_label} {task_name}
{task_desc_label} {task_prompt}

{principles_label}
{principles_text}

{format_instruction}
"""
    elif task_type == TaskType.COMPOSITE:
        intro = prompt_manager.get("decomposition.composite_task.intro", 
                                   min_tasks=MIN_ATOMIC_TASKS, 
                                   max_tasks=max_subtasks)
        task_name_label = prompt_manager.get("decomposition.composite_task.task_name")
        task_desc_label = prompt_manager.get("decomposition.composite_task.task_description")
        principles_label = prompt_manager.get("decomposition.composite_task.principles")
        principles_list = prompt_manager.get_category("decomposition")["composite_task"]["principles_list"]
        format_instruction = prompt_manager.get("decomposition.composite_task.format_instruction")

        principles_text = "\n".join(principles_list)

        decomp_instruction = f"""
{intro}

{task_name_label} {task_name}
{task_desc_label} {task_prompt}

{principles_label}
{principles_text}

{format_instruction}
"""
    else:
        return ""

    return decomp_instruction


def recursive_decompose_plan(
    plan_title: str, repo: TaskRepository = None, max_depth: int = MAX_DECOMPOSITION_DEPTH
) -> Dict[str, Any]:
    """Recursively decompose all eligible tasks in a plan.

    Args:
        plan_title: Plan title.
        repo: Optional repository instance.
        max_depth: Maximum decomposition depth.

    Returns:
        Summary of decomposition rounds and created subtasks.
    """
    if repo is None:
        repo = default_repo

    try:
        plan_tasks = repo.list_plan_tasks(plan_title)

        decomposition_results = []
        processed_tasks = set()  # Avoid repeated decomposition attempts.

        round_count = 0
        while round_count < max_depth:
            round_count += 1
            current_round_decomposed = False

            plan_tasks = repo.list_plan_tasks(plan_title)

            for task in plan_tasks:
                task_id = task.get("id")
                depth = task.get("depth", 0)

                if task_id in processed_tasks:
                    continue

                if depth >= max_depth - 1:
                    continue

                if should_decompose_task(task, repo):
                    result = decompose_task(task_id, repo)
                    if result.get("success"):
                        decomposition_results.append(result)
                        processed_tasks.add(task_id)
                        current_round_decomposed = True

            if not current_round_decomposed:
                break

        return {
            "success": True,
            "plan_title": plan_title,
            "decompositions": decomposition_results,
            "total_tasks_decomposed": len(decomposition_results),
        }

    except Exception as e:
        return {"success": False, "error": str(e)}
