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

    english_doc_terms = (
        "manuscript",
        "paper",
        "review",
        "survey",
        "abstract",
        "introduction",
        "discussion",
        "conclusion",
    )
    english_action_terms = (
        "write",
        "draft",
        "revise",
        "edit",
        "polish",
        "prepare",
        "generate",
        "create",
    )
    chinese_doc_terms = (
        "论文",
        "综述",
        "文章",
        "稿",
        "摘要",
        "引言",
    )
    chinese_action_terms = (
        "写",
        "生成",
        "撰写",
        "起草",
        "改写",
        "润色",
        "准备",
    )
    if any(term in lowered for term in english_doc_terms) and any(
        term in lowered for term in english_action_terms
    ):
        return True
    if any(term in text for term in chinese_doc_terms) and any(
        term in text for term in chinese_action_terms
    ):
        return True

    return False


def literature_backed_review_request(user_message: str) -> bool:
    text = str(user_message or "").strip()
    lowered = text.lower()
    if not text:
        return False

    review_terms = (
        "review",
        "survey",
        "literature review",
        "综述",
    )
    evidence_terms = (
        "reference",
        "references",
        "citation",
        "citations",
        "literature",
        "evidence",
        "参考文献",
        "文献",
        "证据",
    )
    return any(term in lowered for term in review_terms) and (
        any(term in lowered for term in evidence_terms)
        or any(term in text for term in ("参考文献", "文献", "证据"))
    )


def extract_review_topic(user_message: str) -> Optional[str]:
    text = str(user_message or "").strip()
    if not text:
        return None

    patterns = (
        re.compile(r"topic\s*=\s*['\"]([^'\"]+)['\"]", flags=re.IGNORECASE),
        re.compile(r"关于\s*([^，。,.\n]+?)(?:\s*的|\s*[，。,.\n]|$)"),
        re.compile(
            r"(?:about|on)\s+([^,.:\n]+?)(?:\s+(?:review|survey|paper|manuscript|draft|abstract)\b|[,.:\n]|$)",
            flags=re.IGNORECASE,
        ),
    )
    for pattern in patterns:
        match = pattern.search(text)
        if not match:
            continue
        topic = match.group(1).strip().strip("`'\" ")
        if topic:
            return topic
    return None


def requests_abstract_only(user_message: str) -> bool:
    text = str(user_message or "").strip()
    lowered = text.lower()
    if not text:
        return False
    return any(
        token in lowered
        for token in (
            "abstract only",
            "only abstract",
            "just the abstract",
            "abstract first",
            "只写 abstract",
            "只写摘要",
            "只做摘要",
            "仅摘要",
        )
    ) or any(token in text for token in ("只写 abstract", "只写摘要", "只做摘要", "仅摘要"))


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
