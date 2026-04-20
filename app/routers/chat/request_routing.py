"""Deterministic request-tier routing for chat and DeepThink."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence

from .guardrails import (
    extract_task_ids_from_text,
    local_manuscript_assembly_request,
)
from .subject_identity import (
    build_subject_aliases,
    canonicalize_subject_ref,
    subject_identity_matches,
)

RequestTier = Literal["light", "standard", "research", "execute"]
RequestRouteMode = Literal["manual_deepthink", "auto_simple", "auto_deepthink"]
ThinkingVisibility = Literal["visible", "progress", "hidden"]
ThinkingDisplayMode = Literal["full_thinking", "compact_progress", "final_answer", "hidden"]
IntentType = Literal["chat", "execute_task"]
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

# ── Full plan execution phrases ───────────────────────────────────
# Phrases that signal the user wants to execute *all* remaining tasks
# in the plan tree, not just specific task IDs.
_FULL_PLAN_EXECUTION_PHRASES = (
    "execute all",
    "execute the entire plan",
    "execute the whole plan",
    "execute entire plan",
    "execute whole plan",
    "run all tasks",
    "run the entire plan",
    "run the whole plan",
    "complete all tasks",
    "complete the plan",
    "complete entire plan",
    "finish all tasks",
    "finish the plan",
    "start executing",
    "execute them all",
    "execute them",
    "run them all",
    "run them",
    "do them all",
    "do all tasks",
    "go ahead and execute",
    "go ahead and run",
    "let's execute",
    "let's run",
    "let's go",
    "执行整个计划",
    "执行全部任务",
    "执行所有任务",
    "执行它们",
    "执行她们",
    "执行他们",
    "执行这些任务",
    "执行这些",
    "完成整个计划",
    "完成全部任务",
    "完成所有任务",
    "完成它们",
    "完成她们",
    "完成他们",
    "完成这些任务",
    "完成这些",
    "把计划做完",
    "把任务做完",
    "把所有任务做完",
    "把它们做完",
    "把这些做完",
    "全部执行",
    "全部做完",
    "全部完成",
    "都执行",
    "都做了",
    "都做完",
    "都完成",
    "自动执行",
    "自动完成所有",
    "自动执行所有",
    "自动完成计划",
    "自动执行计划",
    "开始执行整个",
    "开始执行全部",
    "开始执行所有",
    "开始完成所有",
    "开始自动执行",
    "开始执行吧",
    "开始吧",
    "开始做吧",
    "开工吧",
    "一键执行",
    "一键完成",
    "跑起来",
    "跑完所有",
    "跑完全部",
)

_FULL_PLAN_STATUS_QUERY_MARKERS = (
    "?",
    "？",
    "吗",
    "么",
    "是否",
    "是不是",
    "已经",
    "状态",
    "进度",
    "完成了",
    "完成了吗",
    "执行了吗",
    "跑完了吗",
    "有没有完成",
)

_FULL_PLAN_IMPERATIVE_CONTEXTS = (
    "请",
    "帮我",
    "麻烦",
    "开始",
    "继续",
    "直接",
    "立即",
    "一键",
    "please",
    "can you",
    "could you",
    "go ahead",
    "let's",
    "lets ",
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
    "优化计划",
    "优化一下",
    "进行优化",
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
    "重新生成",
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
    # Standalone action verbs that signal execution without followthrough cue
    "解压",
    "解包",
    "测试一下",
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
    # Short forms — when plan_bound, "再优化一下" clearly targets the current plan
    "再优化",
    "再优化一下",
    "优化一下吧",
    "进行优化",
    "开始优化",
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
    "分析", "解压", "解开", "解包", "展开", "重新生成", "生成",
    "try", "attempt", "submit", "test", "run", "deploy", "build",
    "fix", "install", "configure",
)

# Chat-only continuations: "继续说" should stay as chat, not escalate to execute
_CHAT_CONTINUATION_PHRASES = (
    "继续说", "继续讲", "继续解释", "继续介绍", "继续聊", "继续讨论",
    "keep talking", "keep explaining", "go on explaining", "continue explaining",
)

# Bound-task execution cues: when a task is already bound (current_task_id),
# these phrases signal the user wants to *execute* the task, not just chat
# about it.  Used by resolve_intent_type to restore execute_task intent so
# DeepThink safeguards (probe-only detection, verification-only replacement)
# remain active.
_BOUND_TASK_EXECUTE_CUES = (
    "继续执行",
    "继续这个任务",
    "继续做",
    "继续解压",
    "继续运行",
    "开始执行",
    "开始做",
    "开始完成任务",
    "开始完成",
    "直接做",
    "直接开始",
    "直接执行",
    "开工",
    "执行这个任务",
    "执行这个",
    "执行它",
    "跑一下",
    "跑一次",
    "再跑",
    "再试",
    "重试",
    "重新执行",
    "重新运行",
    "解压",
    "解包",
    "continue this task",
    "continue executing",
    "execute this task",
    "execute this",
    "execute it",
    "run this task",
    "run this",
    "run it",
    "start executing",
    "start now",
    "go ahead",
    "do it",
    "finish it",
    "try it",
    "try again",
    "retry",
    "rerun",
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

# All registered tools — the LLM sees every tool and decides which to use.
# Inspired by Claude Code: flat tool pool, no per-request filtering.
ALL_TOOLS: List[str] = [
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


@dataclass(frozen=True)
class RequestRoutingDecision:
    request_tier: RequestTier
    request_route_mode: RequestRouteMode
    route_reason_codes: List[str]
    manual_deep_think: bool
    thinking_visibility: ThinkingVisibility
    effective_user_message: str
    intent_type: IntentType
    subject_resolution: Dict[str, Any]
    brevity_hint: bool
    explicit_task_ids: List[int]
    explicit_task_override: bool
    full_plan_execution: bool = False

    @property
    def use_deep_think(self) -> bool:
        return self.request_route_mode != "auto_simple"

    def metadata(self) -> Dict[str, Any]:
        thinking_display_mode: ThinkingDisplayMode
        if self.thinking_visibility == "progress":
            thinking_display_mode = "compact_progress"
        elif self.thinking_visibility == "visible":
            thinking_display_mode = "full_thinking"
        else:
            thinking_display_mode = "hidden"
        payload = {
            "request_tier": self.request_tier,
            "request_route_mode": self.request_route_mode,
            "route_reason_codes": list(self.route_reason_codes),
            "thinking_visibility": self.thinking_visibility,
            "thinking_display_mode": thinking_display_mode,
            "intent_type": self.intent_type,
            "subject_resolution": dict(self.subject_resolution),
            "brevity_hint": self.brevity_hint,
            "explicit_task_ids": list(self.explicit_task_ids),
            "explicit_task_override": self.explicit_task_override,
            "full_plan_execution": self.full_plan_execution,
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
    explicit_task_ids: List[int]
    explicit_task_override: bool
    full_plan_execution: bool = False

    def prompt_metadata(self) -> Dict[str, Any]:
        return {
            "request_tier": self.request_tier,
            "thinking_budget": self.thinking_budget,
            "max_iterations": self.max_iterations,
            "output_bias": self.output_bias,
            "available_tools": list(self.available_tools),
            "intent_type": self.intent_type,
            "explicit_task_ids": list(self.explicit_task_ids),
            "explicit_task_override": self.explicit_task_override,
            "full_plan_execution": self.full_plan_execution,
        }


def get_all_tools() -> List[str]:
    """Return the flat tool pool — all tools always available."""
    return list(ALL_TOOLS)


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
    request_tier, reasons, brevity_hint = classify_request_tier(
        message=effective_user_message,
        history=history,
        context=context,
        plan_id=plan_id,
        current_task_id=current_task_id,
        intent_type=intent_type,
    )
    combined_reasons = list(
        dict.fromkeys(subject_reasons + intent_reasons + reasons)
    )
    explicit_task_ids = extract_task_ids_from_text(effective_user_message)
    explicit_task_override = bool(explicit_task_ids)
    if explicit_task_override:
        combined_reasons.append("explicit_task_override")
        # Explicit task IDs → elevate to execute_task so DeepThink
        # activates bound-task execution logic.
        if intent_type == "chat":
            intent_type = "execute_task"
            combined_reasons.append("intent_execute_task")
        if request_tier != "execute":
            request_tier = "execute"
            combined_reasons.append("tier_elevated_explicit_task")

    context_plan_id = (context or {}).get("plan_id")
    effective_plan_bound = plan_id is not None or context_plan_id is not None

    # Detect imperative full-plan execution requests ("执行整个计划",
    # "execute all tasks", etc.).  Tier-elevate to execute (more iterations)
    # so DeepThink has enough budget to call plan_operation(execute_all).
    #
    # full_plan_execution stays False — the plan_operation tool has an
    # execute_all operation that launches a background job with DAG ordering,
    # artifact manifest, task verification, and deliverable publishing.
    # DeepThink will call it via native tool-calling, which also produces
    # visible thinking steps for the user.  Setting full_plan_execution=True
    # would bypass DeepThink entirely and delegate to PlanExecutor directly.
    full_plan_execution = False
    _execution_keywords_detected = _is_full_plan_execution_request(
        effective_user_message, plan_bound=effective_plan_bound,
    )
    if _execution_keywords_detected:
        combined_reasons.append("plan_execution_hint")
        if request_tier != "execute":
            request_tier = "execute"
            combined_reasons.append("tier_elevated_full_plan")
        if intent_type == "chat":
            intent_type = "execute_task"
            combined_reasons.append("intent_execute_task")

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
            subject_resolution=subject_resolution,
            brevity_hint=brevity_hint,
            explicit_task_ids=explicit_task_ids,
            explicit_task_override=explicit_task_override,
            full_plan_execution=full_plan_execution,
        )
    return RequestRoutingDecision(
        request_tier=request_tier,
        request_route_mode="auto_deepthink",
        route_reason_codes=combined_reasons,
        manual_deep_think=False,
        thinking_visibility="visible",
        effective_user_message=effective_user_message,
        intent_type=intent_type,
        subject_resolution=subject_resolution,
        brevity_hint=brevity_hint,
        explicit_task_ids=explicit_task_ids,
        explicit_task_override=explicit_task_override,
        full_plan_execution=full_plan_execution,
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
    """Classify the request into a tier that controls thinking budget and iterations.

    This function uses keyword heuristics to decide how much resource to
    allocate (thinking budget, max iterations, thinking visibility).  It does
    NOT decide intent — the LLM decides what tools to call.
    """
    text = str(message or "").strip()
    lowered = text.lower()
    collapsed = _NON_WORD_RE.sub("", lowered)
    reasons: List[str] = []
    context_dict = dict(context or {})

    # ── Structural signals ────────────────────────────────────────
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
    has_depth_cue = _contains_any(lowered, _DEPTH_CUES)
    if has_depth_cue:
        reasons.append("depth_cue")

    has_followthrough_cue = _contains_any(lowered, _FOLLOWTHROUGH_PHRASES)
    has_action_verb = _contains_any(lowered, _ACTION_VERB_PHRASES)
    is_chat_continuation = _contains_any(lowered, _CHAT_CONTINUATION_PHRASES)
    followthrough_implies_execute = (
        has_followthrough_cue and has_action_verb and not is_chat_continuation
    )

    has_plan_request = _has_explicit_plan_request(text)
    has_plan_review = _has_explicit_plan_review_request(text, plan_bound=plan_bound, task_bound=task_bound)
    has_plan_optimize = _has_explicit_plan_optimize_request(text, plan_bound=plan_bound, task_bound=task_bound)

    recent_assistant_turn = _last_role(history, "assistant")
    is_brief_followup = bool(recent_assistant_turn) and (
        len(text) <= 24
        or collapsed in {"why", "how", "继续", "然后呢", "接下来", "next", "nextstep", "whatnext"}
    )
    if is_brief_followup:
        reasons.append("brief_followup")

    # ── Tier decision ─────────────────────────────────────────────
    # 1. Execute tier: structural signals or keyword heuristics that
    #    indicate the user wants the LLM to take action (more iterations,
    #    progress display).  This does NOT set intent_type — the LLM
    #    decides what tools to call.
    if intent_type == "execute_task":
        reasons.append("intent_execution")
        return "execute", reasons, is_brief_followup

    if has_attachments:
        reasons.append("execution_keyword")
        return "execute", reasons, is_brief_followup

    if has_plan_request or has_plan_review or has_plan_optimize:
        if has_plan_request:
            reasons.append("plan_request")
        if has_plan_review:
            reasons.append("plan_review")
        if has_plan_optimize:
            reasons.append("plan_optimize")
        return "execute", reasons, is_brief_followup

    if has_execute_keyword or followthrough_implies_execute:
        reasons.append("execution_keyword")
        return "execute", reasons, is_brief_followup

    if plan_bound and has_followthrough_cue and not is_chat_continuation:
        reasons.append("plan_followthrough")
        return "execute", reasons, is_brief_followup

    # 2. Research: literature / time-sensitive cues
    if has_research_cue or has_time_sensitive_cue:
        return "research", reasons, is_brief_followup

    # 2b. Remote status queries
    _REMOTE_STATUS_WORDS = ("状态", "进度", "在跑", "运行", "完成", "status", "running", "progress")
    if (
        re.search(r"(?<!\d)\d{5,}(?!\d)", lowered)
        and _contains_any(lowered, _REMOTE_STATUS_WORDS)
    ):
        reasons.append("remote_status_query")
        return "standard", reasons, is_brief_followup

    # 3. Light: greetings, short social, brief follow-ups
    is_light_exact = collapsed in {
        _NON_WORD_RE.sub("", token.lower()) for token in _LIGHT_EXACT
    }
    is_light_phrase = _contains_any(lowered, _LIGHT_PHRASES)

    if is_light_exact or is_light_phrase:
        reasons.append("light_social")
        return "light", reasons, is_brief_followup

    if is_brief_followup and has_depth_cue:
        reasons.append("brief_followup_depth")
        return "standard", reasons, True

    if is_brief_followup:
        return "light", reasons, True

    is_short_direct_request = bool(text) and (
        len(text) <= 60 or len(text.split()) <= 14
    )
    if is_short_direct_request and not has_depth_cue:
        reasons.append("short_direct_request")
        return "light", reasons, False

    # 4. Default: standard
    reasons.append("default_standard")
    return "standard", reasons, False


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
    """Light requests: always 3 steps (unified with manual deep think)."""
    return 3


def _max_iterations_standard(decision: RequestRoutingDecision) -> int:
    return 3


def _max_iterations_execute(
    decision: RequestRoutingDecision,
    *,
    default_max_iterations: int,
) -> int:
    execute_cap = 6
    if decision.full_plan_execution or decision.explicit_task_override:
        # Explicit task / full plan execution needs to traverse many tasks,
        # so a 6-step cap is too easy to exhaust before followthrough can occur.
        # Use a generous cap (48) to support multi-subtask composite execution.
        execute_cap = 48
    return max(3, min(default_max_iterations, execute_cap))


def build_request_tier_profile(
    decision: RequestRoutingDecision,
    *,
    default_thinking_budget: int,
    simple_thinking_budget: int,
    default_max_iterations: int,
) -> RequestTierProfile:
    common = dict(
        available_tools=get_all_tools(),
        intent_type=decision.intent_type,
        explicit_task_ids=list(decision.explicit_task_ids),
        explicit_task_override=decision.explicit_task_override,
        full_plan_execution=decision.full_plan_execution,
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
    return RequestTierProfile(
        request_tier="execute",
        thinking_budget=max(200, min(default_thinking_budget, 7000)),
        max_iterations=_max_iterations_execute(
            decision,
            default_max_iterations=default_max_iterations,
        ),
        output_bias="task_completion",
        **common,
    )


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


# Phrases that unambiguously reference the *entire* plan, not just the
# current task.  Used to disambiguate when both plan and task are bound.
_UNAMBIGUOUS_FULL_PLAN_MARKERS = (
    "all",
    "entire",
    "whole",
    "every",
    "整个",
    "全部",
    "所有",
    "所有任务",
    "全部任务",
    "整个计划",
)


def _is_unambiguous_full_plan_request(text: str) -> bool:
    """Return True only when the message explicitly references all/entire plan.

    Short cues like "开始吧" / "let's go" / "start executing" are ambiguous
    when a task is bound — they could mean "start this task".  This function
    filters to only the phrases that clearly target the whole plan.
    """
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    return _contains_any_lowered(lowered, _UNAMBIGUOUS_FULL_PLAN_MARKERS)


def _is_full_plan_execution_request(text: str, *, plan_bound: bool) -> bool:
    """Detect if the user wants to execute the entire plan tree.

    Requires the session to be plan-bound; otherwise the request makes
    no sense.
    """
    if not plan_bound:
        return False
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    if not _contains_any_lowered(lowered, _FULL_PLAN_EXECUTION_PHRASES):
        return False
    looks_like_status_question = _contains_any_lowered(
        lowered, _FULL_PLAN_STATUS_QUERY_MARKERS,
    )
    has_imperative_context = _contains_any_lowered(
        lowered, _FULL_PLAN_IMPERATIVE_CONTEXTS,
    )
    if looks_like_status_question and not has_imperative_context:
        return False
    return True


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


def resolve_intent_type(
    *,
    message: str,
    context: Optional[Mapping[str, Any]] = None,
    history: Optional[Sequence[Mapping[str, Any]]] = None,
    subject_resolution: Optional[Mapping[str, Any]] = None,
    plan_id: Optional[int] = None,
    current_task_id: Optional[int] = None,
) -> tuple[IntentType, List[str]]:
    """LLM-first intent classification.

    Only structural signals (attachments, explicit task IDs, bound-task
    execution cues, full plan execution) produce ``execute_task``.
    General keyword-based classification has been removed — the LLM decides
    what to do via tool descriptions.  ``classify_request_tier`` still uses
    keyword heuristics to control thinking budget and iterations independently.
    """
    context_dict = dict(context or {})
    reasons: List[str] = []
    text = str(message or "").strip()
    lowered = text.lower()

    # Attachments → execute (the LLM needs tool iterations to process files)
    if isinstance(context_dict.get("attachments"), list) and context_dict.get("attachments"):
        reasons.append("intent_execute_task")
        reasons.append("has_attachments")
        return "execute_task", reasons

    # Bound-task follow-ups: when a task is already bound (current_task_id)
    # and the user message contains execution-intent keywords, elevate to
    # execute_task so DeepThink safeguards (probe-only detection,
    # verification-only replacement, bound-task final-answer prompt) activate.
    effective_task_id = current_task_id or context_dict.get("current_task_id")
    if effective_task_id is not None and _contains_any(lowered, _BOUND_TASK_EXECUTE_CUES):
        # Exclude chat continuations ("继续说", "keep explaining") which
        # should stay as chat even when task-bound.
        if not _contains_any(lowered, _CHAT_CONTINUATION_PHRASES):
            reasons.append("intent_execute_task")
            reasons.append("bound_task_execution_cue")
            return "execute_task", reasons

    # Everything else → chat.  The LLM will call plan_operation,
    # manuscript_writer, code_executor, etc. as needed based on tool
    # descriptions.  classify_request_tier still elevates the tier
    # (and thus iterations/budget) when it detects execute-like keywords.
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
