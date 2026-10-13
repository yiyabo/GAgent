"""Resolve the target of a plan execution command before entering chat tools."""

from __future__ import annotations

import re
from typing import Any

from .guardrails import extract_task_ids_from_text


_FOLLOWUP = re.compile(
    r"^(?:(?:这样啊|这样|原来如此|明白了|明白|好啊|好|嗯|那|你|请|麻烦|帮我|那就|现在|直接|好的|可以的|可以)[，,\s]*)*"
    r"(?:重新执行|重新运行|重跑|重试|再试(?:一次)?|继续执行|继续运行|继续做|"
    r"执行(?:这个任务|这个|它)?|开始执行|开始|开工|跑一下|跑一次|继续|"
    r"rerun|retry|try again|execute it(?: again)?|run it(?: again)?|continue executing)"
    r"(?:(?:吧|一下|一次|一遍|下))*[\s!！。]*$", re.IGNORECASE,
)
_PLAN_REF = re.compile(r"(?:plan|计划)\s*#?\s*\d+", re.IGNORECASE)


def _task_ids(text):
    return extract_task_ids_from_text(_PLAN_REF.sub("", str(text or "")))


def explicit_task_execution_requested(message):
    text = str(message or "").lower()
    if any(word in text for word in ("?", "？", "吗", "是否", "要不要")):
        return False
    return bool(_task_ids(text)) and any(word in text for word in (
        "执行", "运行", "重跑", "重试", "继续做", "开始做", "做完", "完成任务", "完成 task",
        "execute", "run task", "rerun", "retry", "finish task", "continue task", "complete task"))


def resolve_plan_execution_target(agent: Any, decision: Any) -> tuple[bool, list[int] | None]:
    """Return (handled, targets); None means whole plan, [] means unspecified.

    A recent conversational task reference precedes the session's older task
    binding. Never infer a whole-plan rerun from an unspecified retry command.
    """
    if agent.plan_session.plan_id is None:
        return False, []
    text = str(decision.effective_user_message or "").strip()
    full_plan_hint = decision.full_plan_execution or "plan_execution_hint" in decision.route_reason_codes
    if full_plan_hint:
        from .request_routing import _is_unambiguous_full_plan_request
        if _is_unambiguous_full_plan_request(text) or not _FOLLOWUP.fullmatch(text):
            return True, None
    if decision.explicit_task_override:
        return True, _task_ids(decision.effective_user_message)
    if not _FOLLOWUP.fullmatch(text):
        return False, []
    for item in reversed((getattr(agent, "history", None) or [])[-8:]):
        content = str(item.get("content") or "").strip()
        if not content or content == text:
            continue
        ids = _task_ids(content)
        if ids:
            return True, ids if len(ids) == 1 else []
        if item.get("role") == "user":
            break
    context = getattr(agent, "extra_context", None) or {}
    raw = context.get("current_task_id") or context.get("task_id")
    try:
        task_id = int(raw)
    except (TypeError, ValueError):
        task_id = 0
    return True, [task_id] if task_id > 0 else None if full_plan_hint else []
