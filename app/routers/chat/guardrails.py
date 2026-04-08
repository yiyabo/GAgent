"""Pure / static guardrail predicates extracted from StructuredChatAgent.

Every function here is a former ``@staticmethod`` that does **not** depend on
any instance state.  They are imported back into the class as
``staticmethod(fn)`` delegates so existing call-sites keep working.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, List, Optional, Tuple

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


def local_manuscript_assembly_request(
    user_message: str,
    *,
    plan_bound: bool = False,
    task_bound: bool = False,
) -> bool:
    text = str(user_message or "").strip()
    if not text or not (plan_bound or task_bound):
        return False
    if not explicit_manuscript_request(text):
        return False
    return not literature_backed_review_request(text)


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


_TASK_LABEL_PATTERN = r"(?:任务|task|subtasks?|子任务)"
_TASK_SEPARATOR_PATTERN = r"(?:[/,，、]|和|及|以及|或|or|and)"
_NEGATED_TASK_PREFIX_PATTERNS = (
    r"(?:^|[\s,，、;；。.!?])(?:不要|别|勿|禁止|无需|不用|不必)(?:\s*(?:再|继续))?(?:\s*(?:执行|重跑|跑|做|处理|推进))?\s*$",
    r"(?:^|[\s,，、;；。.!?])(?:不要|别|勿|禁止).*(?:回退成|改写成|当成|视为)\s*$",
    r"(?:^|[\s,，、;；。.!?])(?:do\s+not|don't|dont|skip|avoid)(?:\s+(?:re-?run|run|execute|continue|do))?\s*$",
)
_COMPLETED_TASK_SUFFIX_PATTERNS = (
    r"^(?:已|已经)?完成(?:了)?(?:[\s,，、;；。.!?]|$)",
    r"^(?:已验证通过|验证通过|已通过验证)(?:[\s,，、;；。.!?]|$)",
    r"^(?:already\s+completed|completed|verification\s+passed|verified)(?:[\s,，、;；。.!?]|$)",
    r"^的(?:现有)?(?:产物|输出|结果|状态|交付物|依赖|文件|路径)",
)


def _spans_overlap(existing_spans: List[Tuple[int, int]], start: int, end: int) -> bool:
    for span_start, span_end in existing_spans:
        if start < span_end and end > span_start:
            return True
    return False


def _task_reference_is_negated(text: str, start: int) -> bool:
    window_start = max(0, start - 96)
    context = re.sub(r"\s+", " ", text[window_start:start]).strip()
    if not context:
        return False
    for pattern in _NEGATED_TASK_PREFIX_PATTERNS:
        if re.search(pattern, context, flags=re.IGNORECASE):
            return True
    return False


def _task_reference_is_completion_context(text: str, end: int) -> bool:
    suffix = re.sub(r"\s+", " ", text[end : end + 48]).strip()
    if not suffix:
        return False
    for pattern in _COMPLETED_TASK_SUFFIX_PATTERNS:
        if re.search(pattern, suffix, flags=re.IGNORECASE):
            return True
    return False


def _split_task_id_group(raw_group: str) -> List[str]:
    if not raw_group:
        return []
    return [
        token
        for token in re.split(
            rf"\s*{_TASK_SEPARATOR_PATTERN}\s*(?:{_TASK_LABEL_PATTERN}\s*[#:=：]?\s*)?",
            raw_group,
            flags=re.IGNORECASE,
        )
        if token
    ]


def extract_task_ids_from_text(text: str) -> List[int]:
    if not text:
        return []

    ordered_ids: List[int] = []
    seen: set[int] = set()
    occupied_spans: List[tuple[int, int]] = []

    def _append(raw: str) -> None:
        try:
            task_id = int(raw)
        except (TypeError, ValueError):
            return
        if task_id <= 0 or task_id in seen:
            return
        seen.add(task_id)
        ordered_ids.append(task_id)

    multi_patterns = [
        rf"{_TASK_LABEL_PATTERN}\s*[#:=：]?\s*(\d+(?:\s*{_TASK_SEPARATOR_PATTERN}\s*(?:{_TASK_LABEL_PATTERN}\s*[#:=：]?\s*)?\d+)+)",
        rf"(?<!\d)((?:\d+\s*{_TASK_SEPARATOR_PATTERN}\s*)+\d+)(?!\d)",
    ]
    for pattern in multi_patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            raw_group = str(match.group(1) or "").strip()
            if not raw_group:
                continue
            start, end = match.span()
            if _spans_overlap(occupied_spans, start, end):
                continue
            occupied_spans.append((start, end))
            if _task_reference_is_negated(text, start) or _task_reference_is_completion_context(text, end):
                continue
            for token in _split_task_id_group(raw_group):
                if token:
                    _append(token)

    patterns = [
        rf"(?:task[_\s-]?id|{_TASK_LABEL_PATTERN})\s*[#:=：]?\s*(\d+)",
        r"任务\s*[#:=：]?\s*(\d+)",
        r"第\s*(\d+)\s*(?:个)?任务",
        r"#(\d+)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            start, end = match.span()
            if _spans_overlap(occupied_spans, start, end):
                continue
            occupied_spans.append((start, end))
            if _task_reference_is_negated(text, start) or _task_reference_is_completion_context(text, end):
                continue
            _append(str(match.group(1)))
    return ordered_ids


def extract_task_id_from_text(text: str) -> Optional[int]:
    task_ids = extract_task_ids_from_text(text)
    if task_ids:
        return task_ids[0]
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
        "我现在开始",
        "我这就开始",
        "我开始",
        "马上开始",
        "稍等我去跑一下",
        "正在生成",
    )
    action_tokens = (
        "run",
        "execute",
        "start",
        "draft",
        "write",
        "执行",
        "处理",
        "生成",
        "跑",
        "运行",
        "开始",
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
        "已完成",
        "执行完毕",
        "已生成",
        "已导出",
        "准备就绪",
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
    has_claude_action = any(action.name == "code_executor" for action in actions)
    has_heavy_tool_mix = len(actions) >= 2

    claude_task_texts: List[str] = []
    for action in actions:
        if action.name != "code_executor":
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
