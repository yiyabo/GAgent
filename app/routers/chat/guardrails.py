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

    if re.search(r"(写|撰写|生成|润色|修改|改写|完善).*(论文|稿件)", text):
        return True
    if re.search(r"(论文|稿件).*(写|撰写|生成|润色|修改|改写|完善)", text):
        return True

    return False


def extract_task_id_from_text(text: str) -> Optional[int]:
    if not text:
        return None
    patterns = [
        r"(?:task[_\s-]?id|task)\s*[#:=]?\s*(\d+)",
        r"任务\s*[#:=]?\s*(\d+)",
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
        "完成了",
        "完成吗",
        "完成没",
        "done",
        "status",
        "进度",
        "状态",
        "更新了吗",
        "好了没",
        "结果呢",
    )
    execute_tokens = (
        "执行",
        "运行",
        "开始",
        "继续",
        "重跑",
        "重试",
        "run",
        "execute",
        "start",
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
        "我将",
        "我会",
        "马上",
        "立即",
        "接下来",
        "i will",
        "i'll",
        "starting now",
        "immediately",
    )
    action_tokens = (
        "执行",
        "运行",
        "开始",
        "撰写",
        "写入",
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
        "已完成",
        "完成了",
        "全部完成",
        "已创建",
        "已生成",
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
    # CJK and other non-filesystem characters indicate the "/" is part of
    # natural language (e.g. "创建/拆分"), not a real file path.
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
    normalized = re.sub(r"[\s，。,.!！?？]+", "", raw).lower()
    generic_phrases = {
        "ok",
        "okay",
        "yes",
        "yep",
        "sure",
        "好的",
        "好",
        "可以",
        "可以的",
        "行",
        "行的",
        "创建吧",
        "开始吧",
        "执行吧",
        "继续吧",
        "可以创建吧",
        "可以开始吧",
        "可以执行吧",
        "可以的创建吧",
        "可以的开始吧",
        "可以的执行吧",
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
        "从0到1",
        "完整",
        "全流程",
        "整个任务",
        "综述",
        "论文",
        "项目",
        "task graph",
        "任务图谱",
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
        "完成",
        "实现",
        "产出",
        "交付",
        "构建",
        "build",
        "deliver",
        "complete",
        "implement",
        "finish",
    )
    broad_execution_keywords = (
        "一键",
        "全部",
        "全都",
        "一次性",
        "完整项目",
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
