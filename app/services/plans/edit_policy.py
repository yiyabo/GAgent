"""Keep edits to a persisted task tree separate from execution checklists."""
from __future__ import annotations

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


def user_requested_plan_edit(message: Any) -> bool:
    text = str(message or "").strip()
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
    message = getattr(agent, "_current_user_message", None)
    if message is None:
        return None  # legacy direct service callers have no chat turn
    return user_requested_plan_edit(message)


def blocked_plan_edit(operation: str, allowed: bool | None) -> dict | None:
    if allowed is not False:
        return None
    return {"success": False, "operation": operation, "executed": False,
        "error_code": "plan_edit_not_requested",
        "error": "The user has not requested editing the task tree. Execute or repair the existing task using its original instructions and output requirements. Use an internal todo-list for work steps; do not create, split, delete or rewrite Plan tasks. Ask for an explicit plan-edit request if the tree must change."}


def tool_plan_edit_allowed(tool_context: Any) -> bool | None:
    return (getattr(tool_context, "extra", None) or {}).get("plan_tree_edit_allowed")
