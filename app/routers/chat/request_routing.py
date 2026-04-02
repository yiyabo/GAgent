"""Deterministic request-tier routing for chat and DeepThink."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence

from .subject_identity import (
    build_subject_aliases,
    canonicalize_subject_ref,
    subject_identity_matches,
)

RequestTier = Literal["light", "standard", "research", "execute"]
RequestRouteMode = Literal["manual_deepthink", "auto_simple", "auto_deepthink"]
ThinkingVisibility = Literal["visible", "progress", "hidden"]
PlanRequestMode = Literal["create", "update_bound", "create_new"]
IntentType = Literal[
    "chat",
    "local_read",
    "local_inspect",
    "local_mutation",
    "research",
    "execute_task",
]
CapabilityFloor = Literal[
    "plain_chat",
    "tools",
]
SubjectKind = Literal["none", "file", "directory", "workspace"]

_MANUAL_DEEP_RE = re.compile(r"^\s*/(?:think|deep)\b", re.IGNORECASE)
_NON_WORD_RE = re.compile(r"[\s\W_]+", re.UNICODE)
_PATH_RE = re.compile(
    r"(?P<path>(?:~?/|\.{1,2}/|/)?(?:[\w\-.一-龥]+/)+[\w\-.一-龥]+(?:\.[\w-]+)?)"
)

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
    "inspect log",
    "check the log",
    "create plan",
    "make a plan",
    "decompose",
    "break down",
    "next step",
    "continue this task",
    "continue with",
    "deliverable",
    "submit",
    "帮我做",
    "执行",
    "运行",
    "实现",
    "修复",
    "调试",
    "改代码",
    "写代码",
    "查看日志",
    "检查日志",
    "创建计划",
    "生成计划",
    "拆解",
    "分解",
    "提交",
    "可提交",
    "提交结果",
    "下一步",
    "继续这个任务",
    "继续做",
    # Action verbs that independently signal execution intent
    "尝试",
    "试试",
    "跑一下",
    "跑一次",
    "再试",
    "再跑",
    "再提交",
    "再做",
    "再来一次",
    "重新提交",
    "重新运行",
    "重新执行",
    "重试",
    "try it",
    "try again",
    "retry",
    "rerun",
    "resubmit",
    "attempt",
    "开始执行",
    "开始做",
    "开始完成任务",
    "开始完成",
    "直接做",
    "直接开始",
    "开工",
)

# NOTE: All phrases MUST be lowercase — used with _contains_any_lowered().
_PLAN_REQUEST_PHRASES = (
    "create plan",
    "create a plan",
    "make plan",
    "make a plan",
    "build a plan",
    "draft a plan",
    "generate a plan",
    "give me a plan",
    "turn this into a plan",
    "创建计划",
    "生成计划",
    "制定计划",
    "做个计划",
    "做一个计划",
    "做一份计划",
    "制作计划",
    "制作一个计划",
    "制作plan",
    "制作一个plan",
    "给我一个计划",
    "给我一个plan",
    "帮我做个计划",
    "帮我制定计划",
    "帮我做个plan",
    "帮我制定plan",
    "做个plan",
    "做一个plan",
    "做一份plan",
    "整理成计划",
    "整理成plan",
    "变成计划",
    "变成plan",
)

_PLAN_REQUEST_RE = re.compile(
    r"(?:(?:create|make|build|draft|generate|give me|turn(?: this)? into|创建|生成|制定|制作|做|给我|帮我做|帮我制定)"
    r".{0,12}(?<![a-z])plan(?![a-z]))|(?:(?<![a-z])plan(?![a-z]).{0,12}(?:for me|please|给我|来看看|看看|一下))",
    re.IGNORECASE,
)

_PLAN_REVIEW_PHRASES = (
    "review this plan",
    "review the plan",
    "audit this plan",
    "audit the plan",
    "evaluate this plan",
    "evaluate the plan",
    "score this plan",
    "score the plan",
    "review this task",
    "audit this task",
    "evaluate this task",
    "score this task",
    "plan rubric",
    "rubric score",
    "review这个计划",
    "review这个任务",
    "review一下这个计划",
    "review一下这个任务",
    "审核这个计划",
    "审核这个任务",
    "审核一下这个计划",
    "审核一下这个任务",
    "评估这个计划",
    "评估这个任务",
    "评估一下这个计划",
    "评估一下这个任务",
    "给这个计划打分",
    "给这个任务打分",
    "给这个计划评分",
    "给这个任务评分",
    "rubric",
)

_PLAN_REVIEW_MARKERS = (
    "review",
    "audit",
    "evaluate",
    "score",
    "rubric",
    "审核",
    "评估",
    "评分",
    "打分",
)

_PLAN_OPTIMIZE_PHRASES = (
    "optimize this plan",
    "optimize the plan",
    "update this plan",
    "update the plan",
    "update plan",
    "improve this plan",
    "improve the plan",
    "refine this plan",
    "refine the plan",
    "优化这个计划",
    "优化这个任务",
    "优化一下这个计划",
    "优化一下这个任务",
    "改进这个计划",
    "改进这个任务",
    "完善这个计划",
    "完善这个任务",
    "更新这个计划",
    "更新这个任务",
    "更新一下这个计划",
    "更新一下这个任务",
    "优化plan",
    "优化一下plan",
    "更新plan",
    "更新一下plan",
)

_PLAN_OPTIMIZE_MARKERS = (
    "optimize",
    "optimise",
    "update",
    "improve",
    "refine",
    "优化",
    "改进",
    "完善",
    "更新",
)

_PLAN_TARGET_MARKERS = (
    "plan",
    "计划",
    "task",
    "任务",
    "当前计划",
    "当前任务",
    "这个计划",
    "这个任务",
    "该计划",
    "该任务",
)

_PLAN_STATUS_QUERY_PHRASES = (
    "update me on",
    "give me an update on",
    "status update",
    "progress update",
)

_PLAN_STATUS_QUERY_MARKERS = (
    "status",
    "progress",
    "状态",
    "进度",
    "汇报",
)

# NOTE: All phrases MUST be lowercase — used with _contains_any_lowered().
_PLAN_NEW_REQUEST_PHRASES = (
    "new plan",
    "another plan",
    "fresh plan",
    "新建计划",
    "新建一个计划",
    "新建一个plan",
    "再建一个计划",
    "再建一个plan",
    "重新建一个计划",
    "重新建一个plan",
    "新做一个计划",
    "新做一个plan",
    "另做一个计划",
    "另做一个plan",
    "新的plan",
    "另一个plan",
    "另一个计划",
)

_LOCAL_READ_PHRASES = (
    "read",
    "open",
    "inspect",
    "look inside",
    "look at the file",
    "read the file",
    "show the file",
    "show contents",
    "读取",
    "阅读",
    "打开",
    "看看",
    "看一下",
    "查看",
    "读一下",
    "读这个",
)

_LOCAL_INSPECT_PHRASES = (
    "analyze this file",
    "what's inside",
    "what is inside",
    "show me what's inside",
    "list the data",
    "list the files",
    "inspect the contents",
    "inside",
    "content",
    "contents",
    "schema",
    "column",
    "columns",
    "读取文件",
    "分析这个文件",
    "里面",
    "里头",
    "内容",
    "数据",
    "有哪些数据",
    "都有哪些数据",
    "有啥",
    "看看里面",
    "展开看看",
    "列数据",
    "目录结构",
)

# PhageScope API / remote verification: require product anchor + remote-intent cue so we
# upgrade to research floor (phagescope in allowed_tools) without lifting generic local reads.
_PHAGESCOPE_DOMAIN_MARKERS = (
    "phagescope",
    "phage scope",
)

_PHAGESCOPE_REMOTE_INTENT_MARKERS = (
    "ping",
    "连通",
    "连通性",
    "访问",
    "访问权限",
    "api",
    "token",
    "验证",
    "测试",
    "下载",
    "download",
    "连接",
    "credential",
    "credentials",
    "connectivity",
    "登录",
    "接口",
)

# Follow-up: user asks whether a remote PhageScope task (numeric id) is running / status — without
# repeating the word "phagescope". Requires a PhageScope anchor (subject path or prior cue).
_PHAGESCOPE_TASK_STATUS_PHRASES = (
    "在跑",
    "运行",
    "运行中",
    "状态",
    "进度",
    "查询",
    "咨询",
    "看看",
    "任务",
    "task",  # e.g. "task 38619" without Chinese 任务
    "是否",
    "真的",
    "跑完",
    "完成",
    "下载",
    "输出",
    "验证",
    "结果",
)

# Numeric remote task id + result/output vocabulary (no phagescope keyword / no workspace anchor).
_PHAGESCOPE_TASK_RESULT_PHRASES = (
    "下载",
    "download",
    "输出",
    "结果",
    "验证",
    "quality",
    "annotation",
    "模块",
    "module",
)

_LOCAL_MUTATION_PHRASES = (
    "unzip",
    "extract",
    "decompress",
    "unpack",
    "rename",
    "move",
    "copy",
    "delete",
    "remove",
    "organize",
    "解压",
    "解开",
    "解包",
    "展开",
    "重命名",
    "移动",
    "复制",
    "删除",
    "整理",
    "放回",
    "放到",
    "放在",
)

_ARCHIVE_OBJECT_PHRASES = (
    ".zip",
    " zip",
    "zip包",
    "zip 包",
    "压缩包",
    "压缩文件",
    "archive",
    "archives",
)

_MUTATION_SCOPE_PHRASES = (
    "对应的zip包",
    "对应的 zip 包",
    "对应的压缩包",
    "当前目录",
    "原目录",
    "原地",
    "原处",
    "就放在",
    "放回原处",
    "就在原目录",
    "就在当前目录",
    "这些zip",
    "这些 zip",
    "这些压缩包",
    "这些包",
    "把它们",
    # Follow-up unzip instructions without repeating ".zip" in the same sentence
    "继续解压",
    "重新解压",
    "再解压",
    "解压一下",
    "去解压",
    "接着解压",
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

_DIRECTORY_PHRASES = ("folder", "directory", "目录", "文件夹")

_FOLLOWTHROUGH_PHRASES = (
    "continue",
    "next step",
    "go ahead",
    "do it",
    "finish it",
    "start now",
    "继续",
    "下一步",
    "接着",
    "继续做",
    "继续这个",
    "直接做",
    "开始做",
    "开始执行",
    "开始完成任务",
    "开工",
)

_IMAGE_NOUN_PHRASES = (
    "image",
    "images",
    "picture",
    "pictures",
    "photo",
    "photos",
    "图",
    "图片",
    "这张图",
    "那张图",
    "刚才那张图",
    "上一张图",
    "前一张图",
)

_IMAGE_SHOW_PHRASES = (
    "show",
    "display",
    "view",
    "see",
    "look at",
    "show me",
    "display it",
    "展示",
    "显示",
    "给我看",
    "看看",
    "看一下",
    "再看",
    "贴出来",
)

_IMAGE_REFERENCE_PHRASES = (
    "刚才那张",
    "刚刚那张",
    "上一张",
    "前一张",
    "那张图",
    "那张图片",
    "最新那张",
    "最后那张",
)

_IMAGE_REGENERATE_PHRASES = (
    "regenerate",
    "generate another",
    "new variant",
    "another image",
    "重新生成",
    "重画",
    "再来一张",
    "换个风格",
    "换一种风格",
    "生成一张新的",
    "再生成",
    "新变体",
)

_IMAGE_DISPLAY_EXECUTION_OVERRIDE_PHRASES = (
    "开始执行",
    "开始做",
    "开始完成任务",
    "开始完成",
    "直接做",
    "直接开始",
    "开工",
    "继续做",
    "继续这个任务",
    "继续这个",
    "try it",
    "try again",
    "retry",
    "rerun",
    "resubmit",
    "start now",
    "go ahead",
    "do it",
    "finish it",
)

# Action verbs used for compound detection: followthrough cue + action verb → execute
_ACTION_VERB_PHRASES = (
    "尝试", "试试", "试一下", "提交", "测试", "跑", "运行",
    "执行", "部署", "构建", "修改", "修复", "调试", "安装", "配置",
    "try", "attempt", "submit", "test", "run", "deploy", "build",
    "fix", "install", "configure",
)

# Chat-only continuations: "继续说" should stay as chat, not escalate to execute
_CHAT_CONTINUATION_PHRASES = (
    "继续说", "继续讲", "继续解释", "继续介绍", "继续聊", "继续讨论",
    "keep talking", "keep explaining", "go on explaining", "continue explaining",
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

_REFERENTIAL_CUES = (
    "里面",
    "里头",
    "这个",
    "那个",
    "其中",
    "那里面",
    "看看里面",
    "展开看看",
    "里面有啥",
    "里面有什么",
    "都有哪些数据",
    "有哪些数据",
    "有啥",
    "what's inside",
    "what is inside",
    "inside it",
    "inside there",
    "show me inside",
    "that one",
    "this one",
)

# All tools available when capability_floor == "tools".
# Inspired by Claude Code: the LLM sees every tool and decides which to use.
_ALL_TOOLS: List[str] = [
    "file_operations",
    "document_reader",
    "vision_reader",
    "result_interpreter",
    "web_search",
    "graph_rag",
    "literature_pipeline",
    "review_pack_writer",
    "manuscript_writer",
    "sequence_fetch",
    "bio_tools",
    "deeppl",
    "phagescope",
    "code_executor",
    "plan_operation",
    "deliverable_submit",
    "terminal_session",
    "verify_task",
]

_SUCCESSOR_TOOLSET: Dict[CapabilityFloor, List[str]] = {
    "plain_chat": [],
    "tools": list(_ALL_TOOLS),
}

_CAPABILITY_ORDER: Dict[CapabilityFloor, int] = {
    "plain_chat": 0,
    "tools": 1,
}


@dataclass(frozen=True)
class RequestRoutingDecision:
    request_tier: RequestTier
    request_route_mode: RequestRouteMode
    route_reason_codes: List[str]
    manual_deep_think: bool
    thinking_visibility: ThinkingVisibility
    effective_user_message: str
    intent_type: IntentType
    capability_floor: CapabilityFloor
    simple_channel_allowed: bool
    subject_resolution: Dict[str, Any]
    brevity_hint: bool
    requires_structured_plan: bool = False
    plan_request_mode: Optional[PlanRequestMode] = None
    requires_plan_review: bool = False
    requires_plan_optimize: bool = False

    @property
    def use_deep_think(self) -> bool:
        return self.request_route_mode != "auto_simple"

    def metadata(self) -> Dict[str, Any]:
        payload = {
            "request_tier": self.request_tier,
            "request_route_mode": self.request_route_mode,
            "route_reason_codes": list(self.route_reason_codes),
            "thinking_visibility": self.thinking_visibility,
            "intent_type": self.intent_type,
            "capability_floor": self.capability_floor,
            "simple_channel_allowed": self.simple_channel_allowed,
            "subject_resolution": dict(self.subject_resolution),
            "brevity_hint": self.brevity_hint,
            "requires_structured_plan": self.requires_structured_plan,
            "plan_request_mode": self.plan_request_mode,
            "requires_plan_review": self.requires_plan_review,
            "requires_plan_optimize": self.requires_plan_optimize,
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
    intent_type: IntentType
    capability_floor: CapabilityFloor
    simple_channel_allowed: bool
    requires_structured_plan: bool = False
    plan_request_mode: Optional[PlanRequestMode] = None
    requires_plan_review: bool = False
    requires_plan_optimize: bool = False

    def prompt_metadata(self) -> Dict[str, Any]:
        return {
            "request_tier": self.request_tier,
            "thinking_budget": self.thinking_budget,
            "max_iterations": self.max_iterations,
            "output_bias": self.output_bias,
            "available_tools": list(self.available_tools),
            "intent_type": self.intent_type,
            "capability_floor": self.capability_floor,
            "simple_channel_allowed": self.simple_channel_allowed,
            "requires_structured_plan": self.requires_structured_plan,
            "plan_request_mode": self.plan_request_mode,
            "requires_plan_review": self.requires_plan_review,
            "requires_plan_optimize": self.requires_plan_optimize,
        }


def allowed_tools_for_capability_floor(capability_floor: CapabilityFloor) -> List[str]:
    return list(_SUCCESSOR_TOOLSET.get(capability_floor, []))


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
        strip_manual_deep_prefix(raw_message)
        if _MANUAL_DEEP_RE.match(raw_message)
        else raw_message
    )
    subject_resolution, subject_reasons = resolve_subject_resolution(
        message=effective_user_message,
        history=history,
        context=context,
    )
    intent_type, intent_reasons = resolve_intent_type(
        message=effective_user_message,
        context=context,
        history=history,
        subject_resolution=subject_resolution,
        plan_id=plan_id,
        current_task_id=current_task_id,
    )
    capability_floor, capability_reasons = determine_capability_floor(
        intent_type=intent_type,
    )
    request_tier, reasons, brevity_hint = classify_request_tier(
        message=effective_user_message,
        history=history,
        context=context,
        plan_id=plan_id,
        current_task_id=current_task_id,
        intent_type=intent_type,
    )
    combined_reasons = list(
        dict.fromkeys(subject_reasons + intent_reasons + capability_reasons + reasons)
    )
    simple_channel_allowed = (not manual) and capability_floor == "plain_chat"
    context_plan_id = (context or {}).get("plan_id")
    context_task_id = (context or {}).get("current_task_id")
    effective_plan_bound = plan_id is not None or context_plan_id is not None
    effective_task_bound = current_task_id is not None or context_task_id is not None
    requires_plan_review = _has_explicit_plan_review_request(
        effective_user_message,
        plan_bound=effective_plan_bound,
        task_bound=effective_task_bound,
    )
    requires_plan_optimize = _has_explicit_plan_optimize_request(
        effective_user_message,
        plan_bound=effective_plan_bound,
        task_bound=effective_task_bound,
    )
    plan_request_mode = _resolve_plan_request_mode(
        effective_user_message,
        plan_bound=effective_plan_bound,
    )
    if plan_request_mode is None and effective_plan_bound and (requires_plan_review or requires_plan_optimize):
        plan_request_mode = "update_bound"
    requires_structured_plan = plan_request_mode is not None

    if manual:
        combined_reasons = ["manual_deepthink"] + [
            code for code in combined_reasons if code != "manual_deepthink"
        ]
        return RequestRoutingDecision(
            request_tier=request_tier,
            request_route_mode="manual_deepthink",
            route_reason_codes=combined_reasons,
            manual_deep_think=True,
            thinking_visibility="visible",
            effective_user_message=effective_user_message,
            intent_type=intent_type,
            capability_floor=capability_floor,
            simple_channel_allowed=simple_channel_allowed,
            subject_resolution=subject_resolution,
            brevity_hint=brevity_hint,
            requires_structured_plan=requires_structured_plan,
            plan_request_mode=plan_request_mode,
            requires_plan_review=requires_plan_review,
            requires_plan_optimize=requires_plan_optimize,
        )
    if simple_channel_allowed:
        return RequestRoutingDecision(
            request_tier=request_tier,
            request_route_mode="auto_simple",
            route_reason_codes=combined_reasons,
            manual_deep_think=False,
            thinking_visibility="visible",
            effective_user_message=effective_user_message,
            intent_type=intent_type,
            capability_floor=capability_floor,
            simple_channel_allowed=simple_channel_allowed,
            subject_resolution=subject_resolution,
            brevity_hint=brevity_hint,
            requires_structured_plan=requires_structured_plan,
            plan_request_mode=plan_request_mode,
            requires_plan_review=requires_plan_review,
            requires_plan_optimize=requires_plan_optimize,
        )
    return RequestRoutingDecision(
        request_tier=request_tier,
        request_route_mode="auto_deepthink",
        route_reason_codes=combined_reasons,
        manual_deep_think=False,
        thinking_visibility="progress" if request_tier in {"research", "execute"} else "visible",
        effective_user_message=effective_user_message,
        intent_type=intent_type,
        capability_floor=capability_floor,
        simple_channel_allowed=simple_channel_allowed,
        subject_resolution=subject_resolution,
        brevity_hint=brevity_hint,
        requires_structured_plan=requires_structured_plan,
        plan_request_mode=plan_request_mode,
        requires_plan_review=requires_plan_review,
        requires_plan_optimize=requires_plan_optimize,
    )


def classify_request_tier(
    *,
    message: str,
    history: Optional[Sequence[Mapping[str, Any]]] = None,
    context: Optional[Mapping[str, Any]] = None,
    plan_id: Optional[int] = None,
    current_task_id: Optional[int] = None,
    intent_type: IntentType = "chat",
) -> tuple[RequestTier, List[str], bool]:
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

    has_action_verb = _contains_any(lowered, _ACTION_VERB_PHRASES)
    is_chat_continuation = _contains_any(lowered, _CHAT_CONTINUATION_PHRASES)
    followthrough_implies_execute = (
        has_followthrough_cue and has_action_verb and not is_chat_continuation
    )
    prioritize_image_display = should_prioritize_existing_image_display(
        message,
        context_dict,
        task_bound=task_bound,
        plan_followthrough=bool(plan_bound and has_followthrough_cue),
        followthrough_implies_execute=followthrough_implies_execute,
    )

    if has_execute_keyword and not prioritize_image_display:
        reasons.append("execution_keyword")

    is_light_exact = collapsed in {
        _NON_WORD_RE.sub("", token.lower()) for token in _LIGHT_EXACT
    }
    is_light_phrase = _contains_any(lowered, _LIGHT_PHRASES)
    is_short_direct_request = bool(text) and (
        len(text) <= 60 or len(text.split()) <= 14
    )
    is_simple_question = is_short_direct_request and (
        "?" in text
        or "？" in text
        or any(
            token in lowered
            for token in ("what", "why", "how", "can you", "可以", "怎么", "如何", "是什么")
        )
        or "吗" in text
    )

    if intent_type in {"local_mutation", "execute_task"}:
        reasons.append("intent_execution")
        return "execute", reasons, is_brief_followup

    if intent_type == "research":
        reasons.append("intent_research")
        return "research", reasons, is_brief_followup

    if has_attachments or (has_execute_keyword and not prioritize_image_display):
        return "execute", reasons, is_brief_followup

    if plan_bound and (
        has_followthrough_cue or (has_execute_keyword and not prioritize_image_display)
    ):
        if "plan_bound" not in reasons:
            reasons.append("plan_bound")
        return "execute", reasons, is_brief_followup

    if has_research_cue or has_time_sensitive_cue:
        return "research", reasons, is_brief_followup

    if is_light_exact or is_light_phrase:
        reasons.append("light_social")
        return "light", reasons, is_brief_followup

    if is_brief_followup:
        return "light", reasons, True

    if (is_simple_question or is_short_direct_request) and not has_depth_cue:
        reasons.append("short_direct_request")
        return "light", reasons, False

    reasons.append("default_standard")
    return "standard", reasons, False


def determine_capability_floor(
    *,
    intent_type: IntentType,
) -> tuple[CapabilityFloor, List[str]]:
    """Binary capability: chat (no tools) vs tools (all tools).

    The LLM itself decides which tools to use — no dynamic filtering.
    IntentType is preserved for prompt engineering and request_tier control.
    """
    if intent_type == "chat":
        return "plain_chat", []
    return "tools", [f"capability_{intent_type}"]


def resolve_subject_resolution(
    *,
    message: str,
    history: Optional[Sequence[Mapping[str, Any]]] = None,
    context: Optional[Mapping[str, Any]] = None,
) -> tuple[Dict[str, Any], List[str]]:
    text = str(message or "").strip()
    lowered = text.lower()
    context_dict = dict(context or {})
    reasons: List[str] = []
    active_subject = context_dict.get("active_subject")
    last_subject_action_class = str(context_dict.get("last_subject_action_class") or "").strip().lower()
    current_turn = _current_user_turn_index(history)

    attachments = context_dict.get("attachments")
    if isinstance(attachments, list):
        for item in attachments:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            kind: SubjectKind = "file"
            if item.get("type") in {"directory", "folder"}:
                kind = "directory"
            reasons.append("explicit_attachment_subject")
            canonical_ref = canonicalize_subject_ref(path)
            return (
                {
                    "kind": kind,
                    "canonical_ref": canonical_ref or path,
                    "display_ref": path,
                    "aliases": build_subject_aliases(path, canonical_ref),
                    "source": "explicit",
                    "continuity": "new",
                    "confidence": 0.98,
                },
                reasons,
            )

    explicit_path = _extract_explicit_path(text)
    if explicit_path:
        kind = _infer_subject_kind_from_text(explicit_path, lowered)
        canonical_ref = canonicalize_subject_ref(explicit_path) or explicit_path
        continuity = "new"
        if subject_identity_matches(
            active_subject,
            candidate_ref=canonical_ref,
            candidate_display_ref=explicit_path,
        ):
            continuity = "continued"
        elif isinstance(active_subject, dict):
            active_ref = str(active_subject.get("canonical_ref") or "").strip()
            if active_ref:
                continuity = "shifted"
        reasons.append("explicit_subject")
        return (
            {
                "kind": kind,
                "canonical_ref": canonical_ref,
                "display_ref": explicit_path,
                "aliases": build_subject_aliases(explicit_path, canonical_ref),
                "source": "explicit",
                "continuity": continuity,
                "confidence": 0.96,
            },
            reasons,
        )

    if _should_inherit_active_subject(
        lowered,
        active_subject,
        current_turn,
        last_subject_action_class=last_subject_action_class,
    ):
        inherited = dict(active_subject)
        canonical_ref = canonicalize_subject_ref(
            inherited.get("canonical_ref") or inherited.get("display_ref")
        )
        display_ref = str(inherited.get("display_ref") or canonical_ref).strip()
        kind = str(inherited.get("kind") or "workspace").strip().lower()
        reasons.append("inherited_subject")
        return (
            {
                "kind": kind if kind in {"file", "directory", "workspace"} else "workspace",
                "canonical_ref": canonical_ref,
                "display_ref": display_ref or canonical_ref,
                "aliases": build_subject_aliases(
                    inherited.get("aliases"),
                    canonical_ref,
                    display_ref,
                ),
                "source": "inherited",
                "continuity": "continued",
                "confidence": 0.84,
            },
            reasons,
        )

    return (
        {
            "kind": "none",
            "canonical_ref": None,
            "display_ref": None,
            "source": "none",
            "continuity": "none",
            "confidence": 0.0,
        },
        reasons,
    )


def _max_iterations_light(decision: RequestRoutingDecision) -> int:
    """Plain chat stays 1–2 steps; tool-backed floors need room for tool + synthesis."""
    if decision.capability_floor == "plain_chat":
        return 1 if not decision.manual_deep_think else 2
    return 2 if not decision.manual_deep_think else 3


def _max_iterations_standard(decision: RequestRoutingDecision) -> int:
    if decision.capability_floor == "plain_chat":
        return 2 if decision.manual_deep_think else 1
    return 3 if decision.manual_deep_think else 2


def build_request_tier_profile(
    decision: RequestRoutingDecision,
    *,
    default_thinking_budget: int,
    simple_thinking_budget: int,
    default_max_iterations: int,
) -> RequestTierProfile:
    common = dict(
        available_tools=allowed_tools_for_capability_floor(decision.capability_floor),
        intent_type=decision.intent_type,
        capability_floor=decision.capability_floor,
        simple_channel_allowed=decision.simple_channel_allowed,
        requires_structured_plan=decision.requires_structured_plan,
        plan_request_mode=decision.plan_request_mode,
        requires_plan_review=decision.requires_plan_review,
        requires_plan_optimize=decision.requires_plan_optimize,
    )
    if decision.request_tier == "light":
        return RequestTierProfile(
            request_tier="light",
            thinking_budget=max(80, min(simple_thinking_budget, 400)),
            max_iterations=_max_iterations_light(decision),
            output_bias="short_direct",
            **common,
        )
    if decision.request_tier == "standard":
        return RequestTierProfile(
            request_tier="standard",
            thinking_budget=max(120, min(simple_thinking_budget, 900)),
            max_iterations=_max_iterations_standard(decision),
            output_bias="concise_complete",
            **common,
        )
    if decision.request_tier == "research":
        research_cap = min(default_max_iterations, 8)
        return RequestTierProfile(
            request_tier="research",
            thinking_budget=max(simple_thinking_budget, min(default_thinking_budget, 10000)),
            max_iterations=max(3, research_cap),
            output_bias="evidence_backed",
            **common,
        )
    execute_cap = min(default_max_iterations, 6)
    return RequestTierProfile(
        request_tier="execute",
        thinking_budget=max(200, min(default_thinking_budget, 7000)),
        max_iterations=max(3, execute_cap),
        output_bias="task_completion",
        **common,
    )


def _max_capability(left: CapabilityFloor, right: CapabilityFloor) -> CapabilityFloor:
    if _CAPABILITY_ORDER[left] >= _CAPABILITY_ORDER[right]:
        return left
    return right


def _contains_any(text: str, phrases: Sequence[str]) -> bool:
    haystack = text.lower()
    return any(phrase.lower() in haystack for phrase in phrases)


def _contains_any_lowered(lowered_text: str, phrases: Sequence[str]) -> bool:
    """Like _contains_any but caller guarantees text is already lowercased."""
    return any(phrase in lowered_text for phrase in phrases)


def _has_explicit_plan_request(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    return _contains_any_lowered(lowered, _PLAN_REQUEST_PHRASES) or _contains_any_lowered(
        lowered, _PLAN_NEW_REQUEST_PHRASES
    ) or bool(_PLAN_REQUEST_RE.search(lowered))


def _is_explicit_new_plan_request(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    return _contains_any_lowered(lowered, _PLAN_NEW_REQUEST_PHRASES)


def _resolve_plan_request_mode(
    text: str,
    *,
    plan_bound: bool,
) -> Optional[PlanRequestMode]:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return None
    has_plan = _contains_any_lowered(lowered, _PLAN_REQUEST_PHRASES) or _contains_any_lowered(
        lowered, _PLAN_NEW_REQUEST_PHRASES
    ) or bool(_PLAN_REQUEST_RE.search(lowered))
    if not has_plan:
        return None
    if _contains_any_lowered(lowered, _PLAN_NEW_REQUEST_PHRASES):
        return "create_new"
    if plan_bound:
        return "update_bound"
    return "create"


def _has_explicit_plan_review_request(
    text: str,
    *,
    plan_bound: bool,
    task_bound: bool,
) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered or not (plan_bound or task_bound):
        return False
    if _contains_any_lowered(lowered, _PLAN_REVIEW_PHRASES):
        return True
    if not _contains_any(lowered, _PLAN_REVIEW_MARKERS):
        return False
    return _contains_any(lowered, _PLAN_TARGET_MARKERS)


def _has_explicit_plan_optimize_request(
    text: str,
    *,
    plan_bound: bool,
    task_bound: bool,
) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered or not (plan_bound or task_bound):
        return False
    if _looks_like_plan_status_query(lowered):
        return False
    if _contains_any_lowered(lowered, _PLAN_OPTIMIZE_PHRASES):
        return True
    if not _contains_any(lowered, _PLAN_OPTIMIZE_MARKERS):
        return False
    return _contains_any(lowered, _PLAN_TARGET_MARKERS)


def _looks_like_plan_status_query(lowered: str) -> bool:
    if not lowered or not _contains_any(lowered, _PLAN_TARGET_MARKERS):
        return False
    if _contains_any_lowered(lowered, _PLAN_STATUS_QUERY_PHRASES):
        return True
    if not _contains_any(lowered, ("update", "更新")):
        return False
    return _contains_any(lowered, _PLAN_STATUS_QUERY_MARKERS)


def _has_recent_image_artifacts(context: Optional[Mapping[str, Any]]) -> bool:
    if not context:
        return False
    items = context.get("recent_image_artifacts")
    if not isinstance(items, list):
        return False
    return any(
        isinstance(item, dict) and str(item.get("path") or "").strip()
        for item in items
    )


def requests_image_regeneration(message: str) -> bool:
    lowered = str(message or "").strip().lower()
    if not lowered:
        return False
    return _contains_any(lowered, _IMAGE_REGENERATE_PHRASES)


def requests_existing_image_display(
    message: str,
    context: Optional[Mapping[str, Any]] = None,
) -> bool:
    lowered = str(message or "").strip().lower()
    if not lowered:
        return False
    if requests_image_regeneration(lowered):
        return False
    if context is not None and not _has_recent_image_artifacts(context):
        return False
    has_show = _contains_any(lowered, _IMAGE_SHOW_PHRASES)
    has_image_noun = _contains_any(lowered, _IMAGE_NOUN_PHRASES)
    has_reference = _contains_any(lowered, _IMAGE_REFERENCE_PHRASES)
    return (has_show and has_image_noun) or has_reference


def should_prioritize_existing_image_display(
    message: str,
    context: Optional[Mapping[str, Any]] = None,
    *,
    task_bound: bool = False,
    plan_followthrough: bool = False,
    followthrough_implies_execute: bool = False,
) -> bool:
    lowered = str(message or "").strip().lower()
    if not requests_existing_image_display(lowered, context):
        return False
    if task_bound or plan_followthrough or followthrough_implies_execute:
        return False
    if _contains_any(lowered, _IMAGE_DISPLAY_EXECUTION_OVERRIDE_PHRASES):
        return False
    return True


def _references_prior_subject(lowered: str) -> bool:
    return _contains_any(lowered, _REFERENTIAL_CUES)


def _references_mutation_subject(
    lowered: str,
    *,
    last_subject_action_class: str = "",
) -> bool:
    if last_subject_action_class not in {"read_only", "inspect", "mutation"}:
        return False
    has_mutation_verb = _contains_any(lowered, _LOCAL_MUTATION_PHRASES)
    has_archive_target = _contains_any(lowered, _ARCHIVE_OBJECT_PHRASES)
    has_scope_target = _contains_any(lowered, _MUTATION_SCOPE_PHRASES)
    return has_mutation_verb and (has_archive_target or has_scope_target)


def _extract_explicit_path(text: str) -> Optional[str]:
    if not text:
        return None
    for match in _PATH_RE.finditer(text):
        candidate = str(match.group("path") or "").strip().strip("`'\"")
        if not candidate or "://" in candidate:
            continue
        if candidate in {".", ".."}:
            continue
        if "/" not in candidate and "\\" not in candidate:
            continue
        return candidate.replace("\\", "/")
    return None


def _is_phagescope_remote_verification_intent(lowered: str) -> bool:
    """True when the user is asking to verify PhageScope API/connectivity (not generic phage analysis)."""
    if not _contains_any(lowered, _PHAGESCOPE_DOMAIN_MARKERS):
        return False
    return _contains_any(lowered, _PHAGESCOPE_REMOTE_INTENT_MARKERS)


def _subject_refs_phagescope_workspace(subject: Mapping[str, Any]) -> bool:
    """True when inherited/explicit subject paths live under a phagescope workspace (session continuity)."""
    for key in ("canonical_ref", "display_ref"):
        v = subject.get(key)
        if isinstance(v, str) and "phagescope" in v.replace("\\", "/").lower():
            return True
    aliases = subject.get("aliases")
    if isinstance(aliases, (list, tuple)):
        for a in aliases:
            if isinstance(a, str) and "phagescope" in a.replace("\\", "/").lower():
                return True
    return False


def _text_has_phagescope_remote_task_id(text: str) -> bool:
    """Heuristic: PhageScope remote task ids are typically 5+ digit numbers."""
    return bool(re.search(r"(?<![0-9])(\d{5,})(?![0-9])", text))


def _is_phagescope_task_status_followup(
    lowered: str,
    subject: Mapping[str, Any],
    context_dict: Mapping[str, Any],
) -> bool:
    """User checks a numeric task id + running/status without saying 'phagescope' again; keep research tools."""
    if not _text_has_phagescope_remote_task_id(lowered):
        return False
    if not _contains_any(lowered, _PHAGESCOPE_TASK_STATUS_PHRASES):
        return False
    if _contains_any(lowered, _PHAGESCOPE_DOMAIN_MARKERS):
        return True
    if _subject_refs_phagescope_workspace(subject):
        return True
    active = context_dict.get("active_subject")
    if isinstance(active, dict) and _subject_refs_phagescope_workspace(active):
        return True
    return False


def _is_phagescope_task_result_followup(lowered: str) -> bool:
    """
    Fetch/download/verify pipeline outputs for a numeric remote task id without 'phagescope' or workspace anchor.

    Example: '下载 task 38619 的 quality 和 annotation 输出进行验证' must route to research (phagescope allowed).
    """
    if not _text_has_phagescope_remote_task_id(lowered):
        return False
    return _contains_any(lowered, _PHAGESCOPE_TASK_RESULT_PHRASES)


def resolve_intent_type(
    *,
    message: str,
    context: Optional[Mapping[str, Any]] = None,
    history: Optional[Sequence[Mapping[str, Any]]] = None,
    subject_resolution: Optional[Mapping[str, Any]] = None,
    plan_id: Optional[int] = None,
    current_task_id: Optional[int] = None,
) -> tuple[IntentType, List[str]]:
    text = str(message or "").strip()
    lowered = text.lower()
    context_dict = dict(context or {})
    subject = dict(subject_resolution or {})
    subject_kind = str(subject.get("kind") or "none").strip().lower()
    active_subject = context_dict.get("active_subject")
    last_subject_action_class = str(context_dict.get("last_subject_action_class") or "").strip().lower()
    reasons: List[str] = []

    has_research_cue = _contains_any(lowered, _RESEARCH_PHRASES)
    has_time_sensitive_cue = _contains_any(lowered, _TIME_SENSITIVE_PHRASES)
    has_execute_keyword = _contains_any(lowered, _EXECUTE_PHRASES)
    has_plan_request = _has_explicit_plan_request(text)
    has_local_read_cue = _contains_any(lowered, _LOCAL_READ_PHRASES)
    has_local_inspect_cue = _contains_any(lowered, _LOCAL_INSPECT_PHRASES)
    has_local_mutation_cue = _contains_any(lowered, _LOCAL_MUTATION_PHRASES)
    has_archive_object_cue = _contains_any(lowered, _ARCHIVE_OBJECT_PHRASES)
    has_mutation_scope_cue = _contains_any(lowered, _MUTATION_SCOPE_PHRASES)
    has_image_display_cue = requests_existing_image_display(message, context_dict)
    has_image_regeneration_cue = requests_image_regeneration(message)
    has_attachments = isinstance(context_dict.get("attachments"), list) and bool(
        context_dict.get("attachments")
    )
    has_subject_context = subject_kind != "none" or isinstance(active_subject, dict)
    # Allow follow-up "再解压/继续解压" when the prior turn already touched the same subject path
    # even if subject_resolution for this message is empty (short follow-ups).
    has_local_subject_continuity = last_subject_action_class in {
        "read_only",
        "inspect",
        "mutation",
    }
    mutation_context_ok = has_subject_context or has_local_subject_continuity
    task_bound = current_task_id is not None or context_dict.get("current_task_id") is not None
    plan_bound = plan_id is not None or context_dict.get("plan_id") is not None
    has_plan_review_request = _has_explicit_plan_review_request(
        text,
        plan_bound=plan_bound,
        task_bound=task_bound,
    )
    has_plan_optimize_request = _has_explicit_plan_optimize_request(
        text,
        plan_bound=plan_bound,
        task_bound=task_bound,
    )
    has_followthrough_cue = _contains_any(lowered, _FOLLOWTHROUGH_PHRASES)
    explicit_path = _extract_explicit_path(text)

    # Compound detection: followthrough cue ("继续") + action verb ("尝试") → execute,
    # unless the message is a chat continuation ("继续说", "继续讲").
    has_action_verb = _contains_any(lowered, _ACTION_VERB_PHRASES)
    is_chat_continuation = _contains_any(lowered, _CHAT_CONTINUATION_PHRASES)
    followthrough_implies_execute = (
        has_followthrough_cue and has_action_verb and not is_chat_continuation
    )

    if (
        has_image_regeneration_cue
        and (
            _contains_any(lowered, _IMAGE_NOUN_PHRASES)
            or _has_recent_image_artifacts(context_dict)
        )
    ):
        reasons.extend(["intent_execute_task", "image_regeneration"])
        return "execute_task", reasons

    if should_prioritize_existing_image_display(
        message,
        context_dict,
        task_bound=task_bound,
        plan_followthrough=bool(plan_bound and has_followthrough_cue),
        followthrough_implies_execute=followthrough_implies_execute,
    ):
        reasons.append("intent_show_existing_image")
        return "local_read", reasons

    if has_plan_request:
        reasons.append("intent_plan_request")
        if has_plan_review_request:
            reasons.append("intent_plan_review_request")
        if has_plan_optimize_request:
            reasons.append("intent_plan_optimize_request")
        return "execute_task", reasons

    if has_plan_review_request or has_plan_optimize_request:
        if has_plan_review_request:
            reasons.append("intent_plan_review_request")
        if has_plan_optimize_request:
            reasons.append("intent_plan_optimize_request")
        return "execute_task", reasons

    if has_execute_keyword or (plan_bound and has_followthrough_cue) or followthrough_implies_execute:
        reasons.append("intent_execute_task")
        if followthrough_implies_execute and not has_execute_keyword:
            reasons.append("execution_keyword")
        return "execute_task", reasons

    if (
        mutation_context_ok
        and has_local_mutation_cue
        and (
            has_archive_object_cue
            or has_mutation_scope_cue
            or _references_mutation_subject(
                lowered,
                last_subject_action_class=last_subject_action_class,
            )
        )
    ) or (
        bool(explicit_path)
        and has_local_mutation_cue
        and (has_archive_object_cue or has_mutation_scope_cue)
    ):
        reasons.append("intent_local_mutation")
        return "local_mutation", reasons

    if _is_phagescope_remote_verification_intent(lowered):
        reasons.append("intent_phagescope_remote_verify")
        return "research", reasons

    if _is_phagescope_task_status_followup(lowered, subject, context_dict):
        reasons.append("intent_phagescope_task_status")
        return "research", reasons

    if _is_phagescope_task_result_followup(lowered):
        reasons.append("intent_phagescope_task_result")
        return "research", reasons

    if has_research_cue or has_time_sensitive_cue:
        reasons.append("intent_research")
        return "research", reasons

    if has_attachments:
        reasons.append("intent_local_inspect")
        return "local_inspect", reasons

    if has_image_display_cue:
        reasons.append("intent_show_existing_image")
        return "local_read", reasons

    if subject_kind != "none":
        if has_local_inspect_cue or _references_prior_subject(lowered):
            reasons.append("intent_local_inspect")
            return "local_inspect", reasons
        if has_local_read_cue or subject.get("source") == "explicit":
            reasons.append("intent_local_read")
            return "local_read", reasons
        reasons.append("intent_local_read")
        return "local_read", reasons

    if has_local_inspect_cue:
        reasons.append("intent_local_inspect")
        return "local_inspect", reasons
    if has_local_read_cue:
        reasons.append("intent_local_read")
        return "local_read", reasons

    reasons.append("intent_chat")
    return "chat", reasons


def _infer_subject_kind_from_text(path_text: str, lowered_message: str) -> SubjectKind:
    if _contains_any(lowered_message, _DIRECTORY_PHRASES):
        return "directory"
    if path_text.endswith("/"):
        return "directory"
    basename = path_text.rsplit("/", 1)[-1]
    if "." in basename:
        return "file"
    return "workspace"


def _current_user_turn_index(
    history: Optional[Sequence[Mapping[str, Any]]],
) -> int:
    if not history:
        return 1
    return 1 + sum(
        1 for item in history if str(item.get("role") or "").strip().lower() == "user"
    )


def _should_inherit_active_subject(
    lowered_message: str,
    active_subject: Any,
    current_turn: int,
    *,
    last_subject_action_class: str = "",
) -> bool:
    if not isinstance(active_subject, dict):
        return False
    if not (
        _references_prior_subject(lowered_message)
        or _references_mutation_subject(
            lowered_message,
            last_subject_action_class=last_subject_action_class,
        )
    ):
        return False
    canonical_ref = str(active_subject.get("canonical_ref") or "").strip()
    if not canonical_ref:
        return False
    verification_state = str(active_subject.get("verification_state") or "").strip().lower()
    if verification_state == "stale":
        return False
    try:
        salience = int(active_subject.get("salience", 0))
    except (TypeError, ValueError):
        salience = 0
    if salience <= 0:
        return False
    try:
        last_ref_turn = int(active_subject.get("last_referenced_turn", 0))
    except (TypeError, ValueError):
        last_ref_turn = 0
    if last_ref_turn and (current_turn - last_ref_turn) > 5:
        return False
    return True


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
