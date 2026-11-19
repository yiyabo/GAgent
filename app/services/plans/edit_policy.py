"""Keep edits to a persisted task tree separate from execution checklists."""
from __future__ import annotations

import os
import re
from typing import Any

PLAN_EDITS = frozenset({"create", "bind", "optimize", "update_task"})
PLAN_ACTION_EDITS = frozenset({"create_plan", "clone_plan", "import_plan", "delete_plan", "archive_plan",
    "optimize_plan", "update_plan", "rename_plan", "update_plan_metadata", "set_plan_metadata", "assign_plan"})
TASK_EDITS = frozenset({"create_task", "update_task", "update_task_instruction", "move_task", "delete_task",
    "decompose_task", "bulk_update_tasks", "bulk_delete_tasks"})
_TARGET = re.compile(r"计划|任务|节点|阶段|(?<![a-z])(?:plan|task)(?![a-z])|task[-_ ]tree", re.I)
_EDIT = re.compile(r"创建|构建|新增|添加|增加|插入|删除|移除|修改|调整|更新|编辑|优化|重构|重新规划|拆分|拆解|拆成|拆开|细分|合并|移动|排序|标记|接受|"
    r"\b(?:create|add|delete|remove|edit|modify|update|optimi[sz]e|restructure|split|decompose|merge|move|reorder)\b", re.I)
_NEGATED = re.compile(r"不要|不需要|无需|不能|不允许|禁止|别|do not|don't|without", re.I)
_DEFERRED_PLAN_CHANGE = re.compile(
    r"(?:下(?:一)?(?:条(?:消息)?|轮(?:对话)?|次)|稍后|之后|等我)[^。！？\n]{0,24}?"
    r"(?:决定|确认)\s*(?:是否|要不要)\s*(?:需要\s*)?"
    r"(?:创建|建立|新建|生成|制定|修改|优化)\s*(?:一个|新的)?\s*(?:计划|plan\b)"
    r"|\b(?:decide|confirm)\s+(?:later|(?:in\s+)?(?:(?:my|the)\s+)?next\s+(?:message|turn))\s+"
    r"(?:whether|if)\s+to\s+(?:create|make|start|revise)\s+(?:a\s+|the\s+)?plan\b"
    r"|\b(?:decide|confirm)\s+(?:whether|if)\s+to\s+(?:create|make|start|revise)\s+"
    r"(?:a\s+|the\s+)?plan\b\s+(?:later|in\s+(?:(?:my|the)\s+)?next\s+(?:message|turn))\b",
    re.IGNORECASE,
)


def plan_change_deferred(message: str) -> bool:
    """Recognize an explicit decision to leave the Plan unchanged for this turn."""
    return bool(_DEFERRED_PLAN_CHANGE.search(message))


def user_requested_plan_edit(message: Any) -> bool:
    text = str(message or "").strip()
    if plan_change_deferred(text):
        return False
    if re.match(r"^(?:请问)?(?:怎么|如何|怎样|为什么|是否|要不要|需不需要)|^(?:how|why|should)\b", text, re.I):
        return False
    if re.search(r"[?？]|[吗么]\s*$", text) and not re.match(
        r"^(?:请(?!问)|帮我|麻烦|把|我(?:希望|想|需要)|可以帮我|能(?:不能)?帮我|please\b|can you\b|could you\b)|"
        r"^(?:拆分|拆解|拆成|新增|添加|删除|修改|调整|编辑|优化)", text, re.I
    ):
        return False
    return any(_TARGET.search(clause) and _EDIT.search(clause) and not _NEGATED.search(clause)
        for clause in re.split(r"[，,。.!！;；\n]", text))


def agent_plan_edit_allowed(agent: Any) -> bool | None:
    selected = getattr(agent, "_plan_tree_edit_allowed", None)
    if selected is not None:
        return selected
    if (getattr(agent, "extra_context", None) or {}).get("action_selection") == "model":
        return None  # Chat semantics are selected by its model; never unlock an explicit False fence.
    message = getattr(agent, "_current_user_message", None)
    if message is None:
        return None  # legacy direct service callers have no chat turn
    return user_requested_plan_edit(message)


# A running Plan task may revise unfinished parts of its own Plan, but only
# through explicit optimize changes with a reason (never a generated rewrite,
# a new Plan or a status override), and never by removing the running task.
EXECUTION_REVISION = "execution_revision"
EXECUTION_REVISION_GUIDANCE = (
    "PLAN REVISION DURING EXECUTION: the Plan was drafted before the work started and may turn out to be wrong. "
    "First try hard to complete this task as written: change methods, tools, data sources or parameters, and give "
    "long downloads or computations the time they need. If, after real attempts, the task structure itself cannot "
    "reach the goal (a step is impossible, a needed step is missing, or the order is wrong), revise the unfinished "
    "part of the Plan with plan_operation optimize: an explicit changes list (add_task / update_task / delete_task "
    "/ reorder_task) and a reason that lists what you tried. A revision may change how a goal is reached, never "
    "the bar: keep every threshold, validation set and acceptance criterion at least as strict, and never relabel "
    "a failure as a pass. Do not delete the running task or its parents; new tasks run later in the same job."
)
_REVISION_ACTIONS = frozenset({"add_task", "update_task", "delete_task", "reorder_task", "update_description"})


def execution_edit_mode() -> bool | str:
    """Edit permission for Plan task execution: revision by default, ``False`` when disabled."""
    raw = (os.getenv("PLAN_EXECUTION_REVISION_ENABLED") or "1").strip().lower()
    return False if raw in {"0", "false", "no", "off"} else EXECUTION_REVISION


def _revision_denied(operation: str, error: str) -> dict:
    return {"success": False, "operation": operation, "executed": False,
            "error_code": "plan_revision_not_allowed", "error": error}


def blocked_execution_revision(operation: str, *, changes: Any, reason: Any,
                               protected_task_ids: set[int] | None = None) -> dict | None:
    if operation != "optimize":
        return _revision_denied(operation, "While executing a Plan task you may revise unfinished tasks only with "
            "plan_operation optimize and an explicit changes list. Do not create or bind another Plan or set task "
            "status by hand; status follows execution.")
    if not isinstance(changes, list) or not changes:
        return _revision_denied(operation, "Provide an explicit changes list; a generated rewrite of the Plan is "
            "not allowed during execution.")
    if not str(reason or "").strip():
        return _revision_denied(operation, "Provide reason: which methods were already tried, why the current "
            "task structure cannot meet the goal, and how the revision keeps the original goals and thresholds.")
    protected = protected_task_ids or set()
    for change in changes:
        action = str((change or {}).get("action") or "") if isinstance(change, dict) else ""
        if action not in _REVISION_ACTIONS:
            return _revision_denied(operation, f"Unsupported revision action: {action or '<missing>'}.")
        if action == "delete_task":
            try:
                task_id = change.get("task_id")
                target = int(task_id) if task_id is not None else None
            except (TypeError, ValueError):
                target = None
            if target in protected:
                return _revision_denied(operation, "Do not delete the running task or its parent stages; finish "
                    "or report this task instead.")
    return None


def blocked_plan_edit(operation: str, allowed: bool | str | None, *, changes: Any = None, reason: Any = None,
                      protected_task_ids: set[int] | None = None) -> dict | None:
    if allowed == EXECUTION_REVISION:
        return blocked_execution_revision(operation, changes=changes, reason=reason,
                                          protected_task_ids=protected_task_ids)
    if allowed is not False:
        return None
    return {"success": False, "operation": operation, "executed": False,
        "error_code": "plan_edit_not_requested",
        "error": "The user has not requested creating or editing a persistent Plan/task tree. Answer their current chat request using ordinary tools or an internal todo-list. If executing an existing task, preserve its original instructions and output requirements. Do not create, split, delete or rewrite Plan tasks, and do not announce a blocked plan operation that the user never requested."}


def tool_plan_edit_allowed(tool_context: Any) -> bool | str | None:
    return (getattr(tool_context, "extra", None) or {}).get("plan_tree_edit_allowed")
