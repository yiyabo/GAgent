"""Pure / static guardrail predicates extracted from StructuredChatAgent.

Every function here is a former ``@staticmethod`` that does **not** depend on
any instance state.  They are imported back into the class as
``staticmethod(fn)`` delegates so existing call-sites keep working.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from app.services.llm.structured_response import LLMAction


def explicit_manuscript_request(user_message: str) -> bool:
    text = user_message.strip()
    if not text:
        return False
    lowered = text.lower()

    if re.search(r"\b(manuscript|paper)\b", lowered):
        if re.search(r"\b(write|draft|revise|edit|polish|prepare)\b", lowered):
            return True

    return False


def extract_task_id_from_text(text: str) -> Optional[int]:
    if not text:
        return None
    patterns = [
        r"(?:task[_\s-]?id|task)\s*[#:=]?\s*(\d+)",
        r"#(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        try:
            return int(match.group(1))
        except (TypeError, ValueError):
            continue
    return None


def is_status_query_only(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    status_tokens = (
        "done",
        "status",
        "progress",
        "updated",
        "result",
    )
    execute_tokens = (
        "run",
        "execute",
        "start",
        "continue",
        "retry",
        "rerun",
        "resume",
    )
    has_status = any(token in lowered for token in status_tokens)
    has_execute = any(token in lowered for token in execute_tokens)
    return has_status and not has_execute


def reply_promises_execution(reply_text: str) -> bool:
    lowered = str(reply_text or "").strip().lower()
    if not lowered:
        return False
    promise_tokens = (
        "i will",
        "i'll",
        "starting now",
        "immediately",
        "next",
    )
    action_tokens = (
        "run",
        "execute",
        "start",
        "draft",
        "write",
    )
    return any(token in lowered for token in promise_tokens) and any(
        token in lowered for token in action_tokens
    )


def looks_like_completion_claim(reply_text: str) -> bool:
    lowered = str(reply_text or "").strip().lower()
    if not lowered:
        return False
    claim_tokens = (
        "completed",
        "all required files",
        "files have been created",
        "generated successfully",
    )
    return any(token in lowered for token in claim_tokens)


def extract_declared_absolute_paths(reply_text: str) -> List[str]:
    if not reply_text:
        return []
    pattern = re.compile(r"(/(?:[^\s`\"'<>|])+)")
    paths: List[str] = []
    seen: set[str] = set()
    # CJK and other non-filesystem characters indicate "/" is part of
    # natural language (e.g. "create/decompose"), not a real file path.
    _NON_PATH_RE = re.compile(
        r"[\u2E80-\u9FFF\uF900-\uFAFF\uFE30-\uFE4F\uFF00-\uFFEF\u3000-\u303F\u3040-\u30FF]|[^\x00-\x7F]{2}"
    )
    for match in pattern.findall(reply_text):
        cleaned = match.rstrip(".,;:!?)]}")
        if not cleaned.startswith("/"):
            continue
        # Skip matches that contain CJK or other clearly non-path characters
        if _NON_PATH_RE.search(cleaned):
            continue
        # Must have at least one path separator depth (e.g. /foo/bar)
        if cleaned.count("/") < 2:
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        paths.append(cleaned)
    return paths


def is_task_executable_status(status: Optional[str]) -> bool:
    normalized = str(status or "pending").strip().lower()
    return normalized in {"pending", "failed", "skipped"}


def is_generic_plan_confirmation(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    normalized = re.sub(r"[\s,\.!?]+", "", raw).lower()
    generic_phrases = {
        "ok",
        "okay",
        "yes",
        "yep",
        "sure",
        "createit",
        "startit",
        "executeit",
        "continueit",
    }
    return normalized in generic_phrases


def should_force_plan_first(
    user_message: str,
    tool_actions: Optional[List["LLMAction"]] = None,
) -> bool:
    text = (user_message or "").strip()
    lowered = text.lower()
    if not lowered:
        return False

    project_keywords = (
        "task graph",
        "project",
        "end-to-end",
        "end to end",
        "roadmap",
        "research plan",
        "manuscript",
        "paper draft",
        "review paper",
    )
    action_keywords = (
        "build",
        "deliver",
        "complete",
        "implement",
        "finish",
    )
    broad_execution_keywords = (
        "one-click",
        "all",
        "all-at-once",
        "whole project",
        "full project",
        "entire workflow",
    )

    has_project_signal = any(token in lowered for token in project_keywords)
    has_action_signal = any(token in lowered for token in action_keywords)
    long_request = len(text) >= 80

    actions = list(tool_actions or [])
    has_claude_action = any(action.name == "claude_code" for action in actions)
    has_heavy_tool_mix = len(actions) >= 2

    claude_task_texts: List[str] = []
    for action in actions:
        if action.name != "claude_code":
            continue
        params = action.parameters or {}
        task_text = str(params.get("task") or "").strip().lower()
        if task_text:
            claude_task_texts.append(task_text)

    claude_task_is_broad = any(
        len(task_text) >= 120
        or any(token in task_text for token in project_keywords)
        or any(token in task_text for token in broad_execution_keywords)
        for task_text in claude_task_texts
    )
    user_message_requests_broad_execution = any(
        token in lowered for token in broad_execution_keywords
    )

    if has_project_signal and (has_action_signal or long_request):
        return True
    if has_claude_action and (
        has_project_signal
        or user_message_requests_broad_execution
        or claude_task_is_broad
    ):
        return True
    if has_claude_action and has_heavy_tool_mix and long_request:
        return True
    return False
