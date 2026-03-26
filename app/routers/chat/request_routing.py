"""Deterministic request-tier routing for chat and DeepThink."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence

RequestTier = Literal["light", "standard", "research", "execute"]
RequestRouteMode = Literal["manual_deepthink", "auto_simple", "auto_deepthink"]
ThinkingVisibility = Literal["visible", "progress", "hidden"]

_MANUAL_DEEP_RE = re.compile(r"^\s*/(?:think|deep)\b", re.IGNORECASE)
_NON_WORD_RE = re.compile(r"[\s\W_]+", re.UNICODE)

_LIGHT_EXACT = {
    "你好",
    "您好",
    "嗨",
    "哈喽",
    "在吗",
    "谢谢",
    "感谢",
    "好的",
    "收到",
    "明白",
    "可以",
    "ok",
    "okay",
    "hello",
    "hi",
    "hey",
    "thanks",
    "thankyou",
    "thank you",
}

_LIGHT_PHRASES = (
    "你好",
    "您好",
    "好久不见",
    "最近怎么样",
    "在吗",
    "谢谢",
    "感谢",
    "辛苦了",
    "这个方向怎么样",
    "这个可以吗",
    "这行吗",
    "怎么看",
    "怎么样",
    "what do you think",
    "is this good",
    "is it good",
    "worth it",
    "sounds good",
    "how does this look",
)

_RESEARCH_PHRASES = (
    "search",
    "web search",
    "look up",
    "lookup",
    "find papers",
    "search papers",
    "latest",
    "newest",
    "recent",
    "current",
    "up-to-date",
    "state of the art",
    "paper",
    "papers",
    "literature",
    "citation",
    "citations",
    "cite",
    "references",
    "reference",
    "source",
    "sources",
    "review",
    "survey",
    "compare with sources",
    "benchmark",
    "pubmed",
    "pmc",
    "doi",
    "journal",
    "evidence",
    "最新",
    "最近",
    "当前",
    "文献",
    "论文",
    "引用",
    "参考文献",
    "来源",
    "证据",
    "综述",
    "调研",
    "对比",
    "比较",
    "搜索",
    "检索",
    "查一下",
    "查一查",
    "搜一下",
    "搜一搜",
    "最新进展",
    "最新研究",
    "给我来源",
)

_TIME_SENSITIVE_PHRASES = (
    "2024",
    "2025",
    "2026",
    "today",
    "this year",
    "this month",
    "今年",
    "今天",
    "本月",
)

_EXECUTE_PHRASES = (
    "execute",
    "run ",
    "run it",
    "implement",
    "fix ",
    "debug",
    "patch",
    "edit",
    "write code",
    "open the file",
    "analyze this file",
    "inspect log",
    "check the log",
    "create plan",
    "make a plan",
    "decompose",
    "break down",
    "next step",
    "continue this task",
    "continue with",
    "帮我做",
    "执行",
    "运行",
    "实现",
    "修复",
    "调试",
    "改代码",
    "写代码",
    "读取文件",
    "分析这个文件",
    "查看日志",
    "检查日志",
    "创建计划",
    "生成计划",
    "拆解",
    "分解",
    "下一步",
    "继续这个任务",
    "继续做",
)

_FILE_OR_WORKSPACE_PHRASES = (
    "file",
    "files",
    "folder",
    "directory",
    "workspace",
    "repo",
    "repository",
    "codebase",
    "log",
    "logs",
    "文件",
    "目录",
    "工作区",
    "仓库",
    "代码库",
    "日志",
)

_FOLLOWTHROUGH_PHRASES = (
    "continue",
    "next step",
    "go ahead",
    "do it",
    "finish it",
    "继续",
    "下一步",
    "接着",
    "继续做",
    "继续这个",
)

_DEPTH_CUES = (
    "deeply",
    "in depth",
    "in-depth",
    "detailed",
    "detail",
    "详细",
    "深入",
    "展开",
    "具体说说",
    "详细说说",
    "分析一下",
    "分析下",
)


@dataclass(frozen=True)
class RequestRoutingDecision:
    request_tier: RequestTier
    request_route_mode: RequestRouteMode
    route_reason_codes: List[str]
    manual_deep_think: bool
    thinking_visibility: ThinkingVisibility
    effective_user_message: str

    @property
    def use_deep_think(self) -> bool:
        return self.request_route_mode != "auto_simple"

    def metadata(self) -> Dict[str, Any]:
        payload = {
            "request_tier": self.request_tier,
            "request_route_mode": self.request_route_mode,
            "route_reason_codes": list(self.route_reason_codes),
            "thinking_visibility": self.thinking_visibility,
        }
        if self.thinking_visibility == "progress":
            payload["progress_mode"] = "compact"
        return payload


@dataclass(frozen=True)
class RequestTierProfile:
    request_tier: RequestTier
    thinking_budget: int
    max_iterations: int
    available_tools: List[str]
    output_bias: str

    def prompt_metadata(self) -> Dict[str, Any]:
        return {
            "request_tier": self.request_tier,
            "thinking_budget": self.thinking_budget,
            "max_iterations": self.max_iterations,
            "output_bias": self.output_bias,
            "available_tools": list(self.available_tools),
        }


def manual_deep_think_requested(
    message: str,
    context: Optional[Mapping[str, Any]] = None,
) -> bool:
    if _MANUAL_DEEP_RE.match(str(message or "")):
        return True
    if context and bool(context.get("deep_think_enabled")):
        return True
    return False


def strip_manual_deep_prefix(message: str) -> str:
    text = str(message or "")
    stripped = _MANUAL_DEEP_RE.sub("", text, count=1).lstrip()
    return stripped or text


def resolve_request_routing(
    *,
    message: str,
    history: Optional[Sequence[Mapping[str, Any]]] = None,
    context: Optional[Mapping[str, Any]] = None,
    plan_id: Optional[int] = None,
    current_task_id: Optional[int] = None,
) -> RequestRoutingDecision:
    raw_message = str(message or "")
    manual = manual_deep_think_requested(raw_message, context)
    effective_user_message = (
        strip_manual_deep_prefix(raw_message) if _MANUAL_DEEP_RE.match(raw_message) else raw_message
    )
    request_tier, reasons = classify_request_tier(
        message=effective_user_message,
        history=history,
        context=context,
        plan_id=plan_id,
        current_task_id=current_task_id,
    )
    if manual:
        reasons = ["manual_deepthink"] + [code for code in reasons if code != "manual_deepthink"]
        return RequestRoutingDecision(
            request_tier=request_tier,
            request_route_mode="manual_deepthink",
            route_reason_codes=reasons,
            manual_deep_think=True,
            thinking_visibility="visible",
            effective_user_message=effective_user_message,
        )
    if request_tier in {"research", "execute"}:
        return RequestRoutingDecision(
            request_tier=request_tier,
            request_route_mode="auto_deepthink",
            route_reason_codes=reasons,
            manual_deep_think=False,
            thinking_visibility="progress",
            effective_user_message=effective_user_message,
        )
    return RequestRoutingDecision(
        request_tier=request_tier,
        request_route_mode="auto_simple",
        route_reason_codes=reasons,
        manual_deep_think=False,
        thinking_visibility="visible",
        effective_user_message=effective_user_message,
    )


def classify_request_tier(
    *,
    message: str,
    history: Optional[Sequence[Mapping[str, Any]]] = None,
    context: Optional[Mapping[str, Any]] = None,
    plan_id: Optional[int] = None,
    current_task_id: Optional[int] = None,
) -> tuple[RequestTier, List[str]]:
    text = str(message or "").strip()
    lowered = text.lower()
    collapsed = _NON_WORD_RE.sub("", lowered)
    reasons: List[str] = []
    context_dict = dict(context or {})
    attachments = context_dict.get("attachments")
    has_attachments = isinstance(attachments, list) and len(attachments) > 0
    if has_attachments:
        reasons.append("has_attachments")

    task_bound = current_task_id is not None or context_dict.get("current_task_id") is not None
    if task_bound:
        reasons.append("task_bound")

    plan_bound = plan_id is not None or context_dict.get("plan_id") is not None

    has_research_cue = _contains_any(lowered, _RESEARCH_PHRASES)
    has_time_sensitive_cue = _contains_any(lowered, _TIME_SENSITIVE_PHRASES)
    if has_research_cue:
        reasons.append("research_cue")
    if has_time_sensitive_cue:
        reasons.append("time_sensitive_cue")

    has_execute_keyword = _contains_any(lowered, _EXECUTE_PHRASES)
    has_file_or_workspace_cue = _contains_any(lowered, _FILE_OR_WORKSPACE_PHRASES)
    has_depth_cue = _contains_any(lowered, _DEPTH_CUES)
    if has_execute_keyword:
        reasons.append("execution_keyword")
    if has_file_or_workspace_cue:
        reasons.append("file_or_workspace_cue")
    if has_depth_cue:
        reasons.append("depth_cue")

    recent_assistant_turn = _last_role(history, "assistant")
    is_brief_followup = bool(recent_assistant_turn) and (
        len(text) <= 24
        or collapsed in {"why", "how", "继续", "然后呢", "接下来", "next", "nextstep", "whatnext"}
    )
    if is_brief_followup:
        reasons.append("brief_followup")

    has_followthrough_cue = _contains_any(lowered, _FOLLOWTHROUGH_PHRASES)
    if has_followthrough_cue and "brief_followup" not in reasons:
        reasons.append("followthrough_cue")

    is_light_exact = collapsed in {_NON_WORD_RE.sub("", token.lower()) for token in _LIGHT_EXACT}
    is_light_phrase = _contains_any(lowered, _LIGHT_PHRASES)
    is_short_direct_request = bool(text) and (len(text) <= 60 or len(text.split()) <= 14)
    is_simple_question = is_short_direct_request and (
        "?" in text
        or "？" in text
        or any(token in lowered for token in ("what", "why", "how", "can you", "可以", "怎么", "如何", "是什么"))
        or "吗" in text
    )

    if has_attachments or task_bound or has_execute_keyword or (has_file_or_workspace_cue and not has_research_cue):
        return "execute", reasons

    if plan_bound and (has_followthrough_cue or has_execute_keyword):
        if "plan_bound" not in reasons:
            reasons.append("plan_bound")
        return "execute", reasons

    if has_research_cue or has_time_sensitive_cue:
        return "research", reasons

    if is_light_exact or is_light_phrase:
        reasons.append("light_social")
        return "light", reasons

    if is_brief_followup and not has_file_or_workspace_cue:
        return "light", reasons

    if (is_simple_question or is_short_direct_request) and not has_depth_cue:
        reasons.append("short_direct_request")
        return "light", reasons

    reasons.append("default_standard")
    return "standard", reasons


def build_request_tier_profile(
    decision: RequestRoutingDecision,
    *,
    default_thinking_budget: int,
    simple_thinking_budget: int,
    default_max_iterations: int,
) -> RequestTierProfile:
    if decision.request_tier == "light":
        return RequestTierProfile(
            request_tier="light",
            thinking_budget=max(80, min(simple_thinking_budget, 400)),
            max_iterations=1 if not decision.manual_deep_think else 2,
            available_tools=[],
            output_bias="short_direct",
        )
    if decision.request_tier == "standard":
        return RequestTierProfile(
            request_tier="standard",
            thinking_budget=max(120, min(simple_thinking_budget, 900)),
            max_iterations=2 if decision.manual_deep_think else 1,
            available_tools=[],
            output_bias="concise_complete",
        )
    if decision.request_tier == "research":
        return RequestTierProfile(
            request_tier="research",
            thinking_budget=max(simple_thinking_budget, min(default_thinking_budget, 10000)),
            max_iterations=min(default_max_iterations, 8),
            available_tools=[
                "web_search",
                "graph_rag",
                "document_reader",
                "vision_reader",
                "literature_pipeline",
                "review_pack_writer",
                "manuscript_writer",
                "sequence_fetch",
                "bio_tools",
                "deeppl",
                "phagescope",
                "file_operations",
                "plan_operation",
                "terminal_session",
                "verify_task",
                "claude_code",
            ],
            output_bias="evidence_backed",
        )
    return RequestTierProfile(
        request_tier="execute",
        thinking_budget=max(200, min(default_thinking_budget, 7000)),
        max_iterations=min(default_max_iterations, 6),
        available_tools=[
            "graph_rag",
            "sequence_fetch",
            "claude_code",
            "file_operations",
            "document_reader",
            "vision_reader",
            "bio_tools",
            "phagescope",
            "deeppl",
            "plan_operation",
            "terminal_session",
            "verify_task",
            "web_search",
        ],
        output_bias="task_completion",
    )


def _contains_any(text: str, phrases: Sequence[str]) -> bool:
    haystack = text.lower()
    return any(phrase.lower() in haystack for phrase in phrases)


def _last_role(
    history: Optional[Sequence[Mapping[str, Any]]],
    role: str,
) -> Optional[Mapping[str, Any]]:
    if not history:
        return None
    for item in reversed(history):
        if str(item.get("role") or "").strip().lower() == role:
            return item
    return None
