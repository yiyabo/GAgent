import asyncio
import hashlib
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from app.services.execution.tool_executor import UnifiedToolExecutor
from app.services.foundation.settings import CHAT_HISTORY_ABS_MAX, get_settings
from app.services.response_style import (
    PROFESSIONAL_STYLE_INSTRUCTION,
    sanitize_professional_response_text,
)
from app.services.tool_schemas import build_tool_schemas

logger = logging.getLogger(__name__)

_CJK_CHAR_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_INTERNAL_REASONING_RE = re.compile(
    r"(?:^|\b)(?:the user is asking me|i notice this is just|i should\b|i need to\b|i'm ready to help|continue thinking|thinking about next step)",
    re.IGNORECASE,
)


_BIO_TOOLS_FALLBACK_CATALOG: Dict[str, List[str]] = {
    "seqkit": ["stats", "grep", "seq", "head"],
    "blast": ["blastn", "blastp", "makeblastdb"],
    "prodigal": ["predict", "meta"],
    "hmmer": ["hmmscan", "hmmsearch", "hmmpress", "hmmbuild"],
    "checkv": ["end_to_end", "completeness", "complete_genomes"],
}


def _describe_exception(exc: Exception) -> str:
    exc_type = type(exc).__name__
    message = str(exc).strip()
    if message:
        return f"{exc_type}: {message}"
    return exc_type


def _load_bio_tools_catalog() -> Dict[str, List[str]]:
    config_path = Path(__file__).resolve().parents[2] / "tool_box" / "bio_tools" / "tools_config.json"
    try:
        if not config_path.exists():
            return dict(_BIO_TOOLS_FALLBACK_CATALOG)
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        catalog: Dict[str, List[str]] = {}
        for tool_name, info in raw.items():
            ops = sorted((info or {}).get("operations", {}).keys())
            catalog[str(tool_name)] = [str(op) for op in ops]
        if not catalog:
            return dict(_BIO_TOOLS_FALLBACK_CATALOG)
        return catalog
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to load bio tools catalog for DeepThink prompt: %s", exc)
        return dict(_BIO_TOOLS_FALLBACK_CATALOG)


def _format_bio_tools_catalog(catalog: Dict[str, List[str]]) -> str:
    return "; ".join(
        f"{tool} ({', '.join(ops) if ops else 'no operations'})"
        for tool, ops in sorted(catalog.items())
    )


_BIO_TOOLS_CATALOG = _load_bio_tools_catalog()
_BIO_TOOLS_NAMES = sorted(_BIO_TOOLS_CATALOG.keys())
_BIO_TOOLS_CATALOG_TEXT = _format_bio_tools_catalog(_BIO_TOOLS_CATALOG)
_INTERNAL_ARTIFACT_FILENAMES = {"result.json", "manifest.json", "preview.json"}
_INTERNAL_TOOL_OUTPUT_RE = re.compile(
    r"/job_[^/]+/step_\d+_[^/]+/(?:result|manifest|preview)\.json$",
    re.IGNORECASE,
)
# Native tool steps often prefix JSON: "[file_operations] {...}"
_TOOL_RESULT_PREFIX_RE = re.compile(r"^\[[^\]]*]\s*")
_EXPLORATORY_FILE_OPERATIONS = {"read", "list", "exists", "info"}


def _looks_like_completion_claim_text(reply_text: str) -> bool:
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


@dataclass
class ThinkingStep:
    """Represents a single step in the thinking process."""
    iteration: int
    thought: str
    action: Optional[str]
    action_result: Optional[str]
    self_correction: Optional[str]
    display_text: Optional[str] = None
    kind: str = "reasoning"
    timestamp: datetime = field(default_factory=datetime.now)
    status: str = "thinking"  # thinking, calling_tool, analyzing, done, error
    evidence: List[Dict[str, str]] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None


@dataclass
class DeepThinkResult:
    """The final result of the deep thinking process."""
    final_answer: str
    thinking_steps: List[ThinkingStep]
    total_iterations: int
    tools_used: List[str]
    confidence: float  # 0.0 to 1.0
    thinking_summary: str  # A concise summary for the user
    tool_failures: List[Dict[str, Any]] = field(default_factory=list)
    search_verified: bool = True
    fallback_used: bool = False
    structured_plan_required: bool = False
    structured_plan_satisfied: bool = False
    structured_plan_state: Optional[str] = None
    structured_plan_message: Optional[str] = None
    structured_plan_plan_id: Optional[int] = None
    structured_plan_title: Optional[str] = None
    structured_plan_operation: Optional[str] = None


@dataclass
class TaskExecutionContext:
    task_id: Optional[int] = None
    task_name: Optional[str] = None
    task_instruction: Optional[str] = None
    dependency_outputs: List[Dict[str, Any]] = field(default_factory=list)
    plan_outline: Optional[str] = None
    constraints: List[str] = field(default_factory=list)
    skill_context: Optional[str] = None
    context_summary: Optional[str] = None
    context_sections: List[Dict[str, Any]] = field(default_factory=list)
    paper_context_paths: List[str] = field(default_factory=list)
    # Explicit task selection: set when the user names specific task IDs in the message.
    # When explicit_task_override=True the agent must only execute within the declared
    # set and must NOT fall back to prose status summaries or plan-optimise suggestions.
    explicit_task_ids: List[int] = field(default_factory=list)
    explicit_task_override: bool = False


def detect_reasoning_language(text: str) -> str:
    return "zh" if _CJK_CHAR_RE.search(str(text or "")) else "en"


def _localized_text(language: str, zh: str, en: str) -> str:
    return zh if language == "zh" else en


def _default_deepthink_summary(user_query: str) -> str:
    return _localized_text(
        detect_reasoning_language(user_query),
        "已完成思考整理，准备给出结论。",
        "Finished organizing the reasoning and preparing the answer.",
    )


def sanitize_reasoning_text(
    text: str,
    *,
    language: Optional[str] = None,
    max_chars: Optional[int] = None,
) -> str:
    raw = " ".join(str(text or "").split()).strip()
    if not raw:
        return ""
    raw = _INTERNAL_REASONING_RE.sub("", raw).strip(" ,.;:-")
    if not raw:
        return ""
    if max_chars and max_chars > 0 and len(raw) > max_chars:
        raw = raw[: max_chars - 3].rstrip() + "..."
    if language == "zh":
        raw = raw.replace("  ", " ").strip()
    return raw


def summarize_reasoning_step_display(text: str, *, language: str) -> str:
    raw = sanitize_reasoning_text(text, language=language, max_chars=None)
    if not raw:
        return _localized_text(
            language,
            "分析当前问题，准备下一步",
            "Analyzing the request and preparing the next step",
        )
    lines = [line.strip(" -*\t") for line in raw.splitlines() if line.strip()]
    candidate = lines[0] if lines else raw
    candidate = re.sub(r"^#{1,6}\s*", "", candidate).strip()
    sentence_parts = re.split(r"(?<=[。！？!?;；\.])\s+", candidate)
    candidate = sentence_parts[0].strip() if sentence_parts else candidate
    if len(candidate) > 96:
        candidate = candidate[:93].rstrip() + "..."
    return candidate or _localized_text(
        language,
        "分析当前问题，准备下一步",
        "Analyzing the request and preparing the next step",
    )


def summarize_simple_chat_reasoning(user_message: str) -> str:
    text = str(user_message or "").strip()
    language = detect_reasoning_language(text)
    lowered = text.lower()
    stripped = re.sub(r"[\s!！?？,，。…~～]+", "", text)

    greeting_tokens = {"你好", "您好", "hello", "hi", "hey", "嗨", "哈喽"}
    thanks_tokens = {"谢谢", "感谢", "thanks", "thankyou", "thx"}
    confirm_tokens = {"好的", "ok", "okay", "收到", "明白", "可以"}

    if stripped in greeting_tokens or lowered in greeting_tokens:
        return _localized_text(
            language,
            "识别为问候，准备简洁回应",
            "Recognized a greeting, preparing a concise reply",
        )
    if stripped in thanks_tokens or lowered in thanks_tokens:
        return _localized_text(
            language,
            "识别为致谢，准备简短回应",
            "Recognized gratitude, preparing a brief reply",
        )
    if stripped in confirm_tokens or lowered in confirm_tokens:
        return _localized_text(
            language,
            "识别为简短确认，准备继续协助",
            "Recognized a brief confirmation, preparing to continue helping",
        )
    if any(token in text for token in ["请", "帮我", "怎么", "如何"]) or any(
        token in lowered for token in ["please", "help", "how", "what", "why", "can you"]
    ):
        return _localized_text(
            language,
            "识别为具体请求，准备给出方案",
            "Recognized a concrete request, preparing an answer",
        )
    return _localized_text(
        language,
        "识别为直接问题，准备简洁回答",
        "Recognized a direct question, preparing a concise answer",
    )


def summarize_tool_step_display(step: ThinkingStep, *, language: str) -> str:
    action_raw = step.action or ""
    tool_name = ""
    params: Dict[str, Any] = {}
    try:
        parsed = json.loads(action_raw) if action_raw else {}
        if isinstance(parsed, dict):
            tool_name = str(parsed.get("tool") or "").strip().lower()
            params = (
                parsed.get("params") if isinstance(parsed.get("params"), dict) else {}
            )
    except Exception:
        tool_name = ""
        params = {}

    if tool_name == "web_search":
        query = str(params.get("query") or "").strip()
        if query:
            clipped = query[:40] + ("..." if len(query) > 40 else "")
            return _localized_text(
                language, f"检索资料：{clipped}", f"Searching for: {clipped}"
            )
        return _localized_text(language, "检索资料", "Searching for information")
    if tool_name == "document_reader":
        return _localized_text(language, "阅读文档", "Reading documents")
    if tool_name == "file_operations":
        operation = str(params.get("operation") or "").strip().lower()
        if operation == "read":
            return _localized_text(language, "读取文件", "Reading files")
        if operation == "list":
            return _localized_text(language, "查看目录内容", "Inspecting directory contents")
        return _localized_text(language, "处理文件内容", "Working with files")
    if tool_name == "code_executor":
        return _localized_text(language, "执行代码与分析", "Executing code and analysis")
    if tool_name in {"bio_tools", "phagescope", "deeppl", "sequence_fetch"}:
        return _localized_text(language, "运行分析工具", "Running analysis tools")
    if tool_name == "vision_reader":
        return _localized_text(language, "分析图像内容", "Analyzing visual content")
    if tool_name == "graph_rag":
        return _localized_text(language, "查询知识图谱", "Querying knowledge graph")
    if tool_name == "result_interpreter":
        return _localized_text(language, "汇总分析结果", "Interpreting results")
    if tool_name == "plan_operation":
        return _localized_text(language, "更新计划信息", "Updating the plan")
    if tool_name:
        return _localized_text(language, f"调用工具：{tool_name}", f"Using tool: {tool_name}")
    return _localized_text(language, "处理当前步骤", "Processing the current step")


_PROCESS_ONLY_PATTERNS = (
    "让我先",
    "我先",
    "先收集",
    "先整理",
    "先看一下",
    "先检索",
    "先分析",
    "先确认",
    "先梳理",
    "let me first",
    "i'll first",
    "first i will",
    "first, i'll",
    "let me gather",
    "let me collect",
    "let me review",
)

_MULTI_TOOL_RESULT_LINE_RE = re.compile(
    r"^\[(?P<tool>[^\]]+)\]\s+(?P<payload>\{.*\})$",
    re.DOTALL,
)


def is_process_only_answer(text: str, *, user_query: str = "") -> bool:
    raw = " ".join(str(text or "").split()).strip()
    if not raw:
        return True
    lowered = raw.lower()
    if any(pattern in lowered for pattern in _PROCESS_ONLY_PATTERNS):
        return True
    if len(raw) <= 80 and any(
        token in lowered
        for token in (
            "collect latest evidence",
            "gather latest evidence",
            "collect the latest",
            "review the latest",
            "收集最新",
            "收集文献",
            "整理资料",
            "继续收集",
        )
    ):
        return True
    question = str(user_query or "").strip()
    if question and raw == question:
        return True
    return False


def build_user_visible_step(
    step: ThinkingStep,
    *,
    language: str,
    preserve_thought: bool = False,
) -> Dict[str, Any]:
    display_text = str(step.display_text or "").strip()
    kind = str(step.kind or "reasoning").strip() or "reasoning"

    if step.action:
        kind = "tool"
        if not display_text:
            display_text = summarize_tool_step_display(step, language=language)
    elif not display_text:
        display_text = summarize_reasoning_step_display(
            step.thought, language=language
        )

    return {
        "iteration": step.iteration,
        # None signals the frontend to clear any delta-accumulated thought for this step.
        # An empty string "" would be treated as "no update" by the merge logic, so
        # we use null/None to explicitly communicate "discard the accumulated content".
        "thought": str(step.thought or "") if preserve_thought else None,
        "display_text": display_text,
        "kind": kind,
        "action": step.action,
        "action_result": step.action_result,
        "evidence": step.evidence,
        "status": step.status,
        "started_at": step.started_at.isoformat() if step.started_at else None,
        "finished_at": step.finished_at.isoformat() if step.finished_at else None,
        "timestamp": step.timestamp.isoformat() if step.timestamp else None,
        "self_correction": step.self_correction,
    }


class DeepThinkProtocolError(RuntimeError):
    """Raised when DeepThink output violates the required JSON protocol."""


class DeepThinkAgent:
    """
    Agent that performs multi-step reasoning and tool calling before answering.
    Supports streaming output for real-time display of thinking process.
    """

    DEFAULT_TOOL_TIMEOUT = 60
    FINAL_STREAM_CHUNK_CHARS = 30
    FINAL_STREAM_DELAY_SEC = 0.05  # 50 ms — lets the network flush each chunk separately
    MAX_IDENTICAL_TOOL_CALL_CYCLES = 4
    EXTERNAL_RETRIABLE_TOOLS = frozenset({"web_search", "literature_pipeline"})
    MAX_EXTERNAL_TOOL_RETRIES = 1
    MAX_TOOL_RESULT_TEXT_CHARS = 12_000
    MAX_FILE_OPERATION_LIST_SAMPLE_ITEMS = 40

    ARTIFACT_PATH_RE = re.compile(
        r'(?:saved?|writ(?:ten|e)|created?|generated?|output|produced?|exported?)\s+'
        r'(?:to|at|in|as|file)?\s*[:\-]?\s*'
        r'[`"\']?(/[^\s`"\'<>]+\.\w{1,6})[`"\']?',
        re.IGNORECASE,
    )
    BARE_PATH_RE = re.compile(
        r'(/(?:[\w._-]+/)+[\w._-]+\.(?:csv|tsv|xlsx|png|jpg|jpeg|pdf|svg|html|json|txt|fasta|fa|fq|fastq|gff|bed))\b'
    )
    URL_RE = re.compile(r"https?://[^\s`\"'<>]+", re.IGNORECASE)

    def __init__(
        self,
        llm_client: Any,
        available_tools: List[str],
        tool_executor: Callable[[str, Dict[str, Any]], Any],
        max_iterations: int = 10,
        tool_timeout: int = DEFAULT_TOOL_TIMEOUT,
        cancel_event: Optional[asyncio.Event] = None,
        on_thinking: Optional[Callable[[ThinkingStep], Any]] = None,
        on_thinking_delta: Optional[Callable[[int, str], Any]] = None,
        on_final_delta: Optional[Callable[[str], Any]] = None,
        on_tool_start: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
        on_tool_result: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
        on_artifact: Optional[Callable[[Dict[str, Any]], Any]] = None,
        enable_thinking: bool = True,
        thinking_budget: int = 10000,
        on_reasoning_delta: Optional[Callable[[int, str], Any]] = None,
        steer_drain: Optional[Callable[[], List[str]]] = None,
        on_steer_ack: Optional[Callable[[str, int], Any]] = None,
        on_tool_progress: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
        request_profile: Optional[Dict[str, Any]] = None,
    ):
        self.llm_client = llm_client
        self.request_profile = dict(request_profile or {})
        self.available_tools = self._sanitize_available_tools(list(available_tools or []))
        self.tool_executor = tool_executor
        self.max_iterations = max_iterations
        self.tool_timeout = tool_timeout
        self.cancel_event = cancel_event
        self.on_thinking = on_thinking
        self.on_thinking_delta = on_thinking_delta
        self.on_final_delta = on_final_delta
        self.on_tool_start = on_tool_start
        self.on_tool_result = on_tool_result
        self.on_artifact = on_artifact
        self.enable_thinking = enable_thinking
        self.thinking_budget = thinking_budget
        self.on_reasoning_delta = on_reasoning_delta
        self.steer_drain = steer_drain
        self.on_steer_ack = on_steer_ack
        self.on_tool_progress = on_tool_progress
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._skip_current_step = False

    def pause(self) -> None:
        self._pause_event.clear()

    def resume(self) -> None:
        self._pause_event.set()

    def skip_step(self) -> None:
        self._skip_current_step = True

    def _supports_native_tools(self) -> bool:
        return hasattr(self.llm_client, "stream_chat_with_tools_async") and callable(
            getattr(self.llm_client, "stream_chat_with_tools_async")
        )

    def _sanitize_available_tools(self, available_tools: List[str]) -> List[str]:
        ordered: List[str] = []
        seen: set[str] = set()
        for tool in available_tools:
            name = str(tool or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            ordered.append(name)

        if (
            self._request_tier() == "execute"
            and str(self.request_profile.get("intent_type") or "").strip().lower() == "execute_task"
            and not self._requires_structured_plan()
            and not self._required_bound_plan_operations()
        ):
            ordered = [tool for tool in ordered if str(tool).strip().lower() != "plan_operation"]

        return ordered

    def _request_tier(self) -> str:
        return str(self.request_profile.get("request_tier") or "").strip().lower()

    def _capability_floor(self) -> str:
        return str(self.request_profile.get("capability_floor") or "").strip().lower()

    def _requires_structured_plan(self) -> bool:
        return bool(self.request_profile.get("requires_structured_plan"))

    def _plan_request_mode(self) -> str:
        return str(self.request_profile.get("plan_request_mode") or "").strip().lower()

    def _requires_plan_review(self) -> bool:
        return bool(self.request_profile.get("requires_plan_review"))

    def _requires_plan_optimize(self) -> bool:
        return bool(self.request_profile.get("requires_plan_optimize"))

    def _required_bound_plan_operations(self) -> List[str]:
        ops: List[str] = []
        if self._requires_plan_review():
            ops.append("review")
        if self._requires_plan_optimize():
            ops.append("optimize")
        return ops

    @staticmethod
    def _coerce_positive_int(value: Any) -> Optional[int]:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    def _current_plan_id(self) -> Optional[int]:
        return self._coerce_positive_int(self.request_profile.get("current_plan_id"))

    def _current_plan_title(self) -> Optional[str]:
        title = str(self.request_profile.get("current_plan_title") or "").strip()
        return title or None

    def _is_research_or_execute(self) -> bool:
        return self._request_tier() in {"research", "execute"}

    def _is_brief_execute_followup(self) -> bool:
        tier = self._request_tier()
        intent_type = str(self.request_profile.get("intent_type") or "").strip().lower()
        brevity_hint = bool(self.request_profile.get("brevity_hint"))
        if tier != "execute" or not brevity_hint:
            return False
        return intent_type in {
            "execute_task",
            "local_mutation",
            "local_read",
            "local_inspect",
        }

    def _is_execute_task_request(self) -> bool:
        return (
            self._request_tier() == "execute"
            and str(self.request_profile.get("intent_type") or "").strip().lower() == "execute_task"
        )

    def _has_bound_task_context(self, task_context: Optional[TaskExecutionContext]) -> bool:
        if task_context and (
            task_context.task_id is not None or str(task_context.task_instruction or "").strip()
        ):
            return True
        return self._coerce_positive_int(self.request_profile.get("current_task_id")) is not None

    def _current_bound_task_id(
        self,
        task_context: Optional[TaskExecutionContext],
    ) -> Optional[int]:
        request_task_id = self._coerce_positive_int(self.request_profile.get("current_task_id"))
        context_task_id = (
            self._coerce_positive_int(task_context.task_id)
            if task_context and task_context.task_id is not None
            else None
        )
        if (
            request_task_id is not None
            and context_task_id is not None
            and request_task_id != context_task_id
            and self._explicit_task_override_active(task_context)
        ):
            return request_task_id
        if context_task_id is not None:
            return context_task_id
        return request_task_id

    def _explicit_task_override_active(
        self,
        task_context: Optional[TaskExecutionContext],
    ) -> bool:
        return bool(
            getattr(task_context, "explicit_task_override", False)
            if task_context is not None
            else False
        ) or bool(self.request_profile.get("explicit_task_override"))

    def _pending_scope_task_ids(self) -> List[int]:
        raw = self.request_profile.get("pending_scope_task_ids")
        if not isinstance(raw, list):
            return []
        pending: List[int] = []
        for item in raw:
            parsed = self._coerce_positive_int(item)
            if parsed is not None:
                pending.append(parsed)
        return pending

    @staticmethod
    def _is_exploratory_file_operation_call(tool_result: Dict[str, Any]) -> bool:
        if str(tool_result.get("tool_name") or "").strip().lower() != "file_operations":
            return False
        params = tool_result.get("tool_params")
        if not isinstance(params, dict):
            return False
        operation = str(params.get("operation") or "").strip().lower()
        return operation in _EXPLORATORY_FILE_OPERATIONS

    # Tools that actually execute code / external commands.  Only these
    # should set ``had_real_execution_tool`` so that post-execution messages
    # and nudge suppression are accurate.  Coordination tools like
    # ``plan_operation`` and observation tools like ``file_operations`` are
    # deliberately excluded.
    _CODE_EXECUTION_TOOLS: set[str] = {
        "code_executor",
        "bio_tools",
        "terminal_session",
        "deeppl",
        "phagescope",
        "result_interpreter",
    }

    @staticmethod
    def _is_observation_only_tool_call(tool_result: Dict[str, Any]) -> bool:
        """Check if a tool call is read-only / observation-only.

        Uses the tool registry's ``is_read_only`` metadata as the primary
        signal, falling back to the declarative ``_TOOL_METADATA`` dict when
        tools are not yet registered (e.g. in unit tests).  ``file_operations``
        gets special operation-level handling since it mixes read and write ops.
        """
        tool_name = str(tool_result.get("tool_name") or "").strip().lower()

        # file_operations is NOT read-only at the tool level (it can write/delete),
        # but specific operations like read/list/exists/info are observation-only.
        if tool_name == "file_operations":
            return DeepThinkAgent._is_exploratory_file_operation_call(tool_result)

        # Primary: consult the live tool registry.
        # Lazy import to avoid circular dependency:
        #   deep_think_agent → tool_box → plan_executor → deep_think_agent
        from tool_box.tools import get_tool_registry
        registry = get_tool_registry()
        tool_def = registry.get_tool(tool_name)
        if tool_def is not None:
            return tool_def.is_read_only

        # Fallback: consult the declarative metadata dict via the public API
        # (covers test environments where register_all_tools() hasn't been called).
        from tool_box.tool_registry import get_tool_orchestration_metadata
        meta = get_tool_orchestration_metadata(tool_name)
        if meta:
            return meta.get("is_read_only", False)

        return False

    @staticmethod
    def _detect_partial_completion_in_tool_results(
        tool_results: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Scan tool_results for a code_executor call that flagged partial completion.

        Returns a dict with keys ``partial_ratio``, ``produced_files``,
        ``task_directory_full`` when partial completion is detected; otherwise
        ``None``.
        """
        for item in tool_results:
            if item.get("tool_name") != "code_executor":
                continue
            raw_text = item.get("tool_result_text") or ""
            try:
                payload = json.loads(raw_text) if isinstance(raw_text, str) else raw_text
            except (json.JSONDecodeError, TypeError):
                continue
            # The payload might be nested: {"success": ..., "result": {actual_payload}}
            inner = payload
            if isinstance(payload, dict) and "result" in payload and isinstance(payload["result"], dict):
                inner = payload["result"]
            if not isinstance(inner, dict):
                continue
            if inner.get("partial_completion_suspected"):
                return {
                    "partial_ratio": inner.get("partial_ratio"),
                    "produced_files": inner.get("produced_files", []),
                    "task_directory_full": inner.get("task_directory_full", ""),
                }
        return None

    def _build_partial_completion_retry_nudge(
        self,
        partial_info: Dict[str, Any],
        *,
        task_context: Optional["TaskExecutionContext"],
        user_query: str,
        retry_count: int,
    ) -> str:
        """Build a bilingual nudge instructing the agent to retry remaining items.

        Follows the same pattern as ``_build_probe_only_followthrough_nudge``.
        """
        language = detect_reasoning_language(user_query)
        ratio = partial_info.get("partial_ratio", "?/?")
        produced = partial_info.get("produced_files", [])
        task_dir = partial_info.get("task_directory_full", "")

        produced_hint = ""
        if produced:
            # Show at most 15 file basenames to keep the nudge compact
            names = [p.rsplit("/", 1)[-1] if "/" in p else p for p in produced[:15]]
            produced_hint = ", ".join(names)
            if len(produced) > 15:
                produced_hint += f" … ({len(produced)} total)"

        task_bits: List[str] = []
        if task_context:
            if task_context.task_id is not None:
                task_bits.append(f"Task ID={task_context.task_id}")
            if task_context.task_name:
                task_bits.append(f"Task Name={task_context.task_name}")
        task_label = " | ".join(task_bits) if task_bits else ""

        zh = (
            f"⚠️ 上一次 code_executor 执行检测到 **部分完成**：仅处理了 {ratio} 项。\n"
            f"（已生成文件: {produced_hint or '无'}）\n"
            f"这是第 {retry_count} 次重试提示。请按以下步骤操作：\n"
            f"1. 检查 results/ 目录，确认哪些项已经完成（从已有输出文件推断）。\n"
            f"2. 再次调用 `code_executor`，**仅处理尚未完成的剩余项**。\n"
            f"3. 新结果 **追加** 到 results/ 目录，不要覆盖已有文件。\n"
            f"4. 在所有项处理完成之前，**不要提交最终答案**。\n"
        )
        en = (
            f"⚠️ The previous code_executor run detected **partial completion**: only {ratio} items were processed.\n"
            f"(Produced files so far: {produced_hint or 'none'})\n"
            f"This is retry nudge #{retry_count}. Follow these steps:\n"
            f"1. Check the results/ directory to determine which items are already done (infer from existing output files).\n"
            f"2. Call `code_executor` again for ONLY the remaining unfinished items.\n"
            f"3. Append new results to results/ — do NOT overwrite existing files.\n"
            f"4. Do NOT submit a final answer until ALL items have been processed.\n"
        )
        base = _localized_text(language, zh, en)
        if task_dir:
            base += f"\nWork directory: {task_dir}"
        if task_label:
            base += f"\n{task_label}"
        return base

    def _is_probe_only_execution_cycle(
        self,
        tool_results: List[Dict[str, Any]],
        *,
        task_context: Optional[TaskExecutionContext],
    ) -> bool:
        if not tool_results:
            return False
        if not self._is_execute_task_request() or not self._has_bound_task_context(task_context):
            return False
        return all(self._is_observation_only_tool_call(item) for item in tool_results)

    @staticmethod
    def _is_verification_only_tool_result_cycle(
        tool_results: Sequence[Dict[str, Any]],
    ) -> bool:
        if not tool_results:
            return False
        return all(
            str(item.get("tool_name") or "").strip().lower() == "verify_task"
            for item in tool_results
        )

    def _verification_only_cycle_replacement_task_id(
        self,
        executable_calls: Sequence[Any],
        *,
        task_context: Optional[TaskExecutionContext],
        had_real_execution_tool: bool,
    ) -> Optional[int]:
        if had_real_execution_tool or not executable_calls:
            return None
        if not self._is_execute_task_request() or not self._has_bound_task_context(task_context):
            return None
        if not all(
            str(getattr(call, "name", "") or "").strip().lower() == "verify_task"
            for call in executable_calls
        ):
            return None
        return self._current_bound_task_id(task_context)

    def _build_probe_only_followthrough_nudge(
        self,
        *,
        task_context: Optional[TaskExecutionContext],
        user_query: str,
        stage: int = 1,
    ) -> str:
        language = detect_reasoning_language(user_query)
        task_bits: List[str] = []
        if task_context:
            if task_context.task_id is not None:
                task_bits.append(f"Task ID={task_context.task_id}")
            if task_context.task_name:
                task_bits.append(f"Task Name={task_context.task_name}")
            if task_context.task_instruction:
                task_bits.append(
                    f"Task Instruction={self._clip_reference_text(task_context.task_instruction, limit=400)}"
                )
        task_hint = "\n".join(task_bits)
        is_explicit_override = self._explicit_task_override_active(task_context)
        if stage >= 2:
            if is_explicit_override:
                # For explicit task override requests, do NOT suggest BLOCKED_DEPENDENCY.
                # The user explicitly asked for this task — execute it, incorporating
                # prerequisite work if needed.
                zh = (
                    "这是连续第 2 次只做观察型探查而没有真正执行任务。\n"
                    "用户已明确要求执行这个任务，不允许返回 BLOCKED_DEPENDENCY。\n"
                    "下一步必须立即调用真正的执行工具（如 `code_executor` 或任务对应的分析工具）。\n"
                    "如果上游数据或前置产物不存在，请在本次执行中一并生成或补齐所需的前置数据。\n"
                    "不要继续做目录清点、文档浏览或结果浏览式探查。"
                )
                en = (
                    "This is the second consecutive observation-only probe without actually executing the bound task.\n"
                    "The user explicitly requested this task execution — you MUST NOT return BLOCKED_DEPENDENCY.\n"
                    "Your next step MUST call a real execution tool such as `code_executor` or the task-specific analysis tool immediately.\n"
                    "If upstream data or prerequisite outputs are not available, incorporate the prerequisite processing steps within this execution.\n"
                    "Do not continue with directory inventory, document browsing, or result-browsing probes."
                )
            else:
                zh = (
                    "这是连续第 2 次只做观察型探查而没有真正执行任务。\n"
                    "下一步必须二选一：\n"
                    "1. 立即调用真正的执行工具（如 `code_executor` 或任务对应的分析工具）；\n"
                    "2. 若缺少上游交付物或关键输入，直接给出 `BLOCKED_DEPENDENCY` 结论，明确说明缺什么、为什么当前 task 不能继续。\n"
                    "不要继续做目录清点、文档浏览或结果浏览式探查。\n"
                    "不要把当前 task 偷偷改写成前序任务或全量预处理，除非 task 指令明确授权补跑前置步骤。"
                )
                en = (
                    "This is the second consecutive observation-only probe without actually executing the bound task.\n"
                    "Your next step MUST do exactly one of the following:\n"
                    "1. Call a real execution tool such as `code_executor` or the task-specific analysis tool immediately.\n"
                    "2. If required upstream deliverables or key inputs are missing, return a `BLOCKED_DEPENDENCY` conclusion that states what is missing and why the current task cannot proceed.\n"
                    "Do not continue with directory inventory, document browsing, or result-browsing probes.\n"
                    "Do not silently rewrite the current task into an upstream preprocessing task unless the task instruction explicitly authorizes backfilling the prerequisite work."
                )
        else:
            zh = (
                "这是一个已绑定任务的执行请求。你刚才只做了只读目录/文件探查。\n"
                "下一步不要继续做目录清点式 `file_operations` 或只读文档探查。\n"
                "请直接推进任务执行：优先调用真正的执行工具（如 `code_executor` 或该任务对应的分析工具）。\n"
                "如果确实还缺一个关键文件，最多再读取一个直接相关的具体文件，然后立刻执行。\n"
                "不要以“继续探查目录”“需要先看看结构”为理由结束本轮。"
            )
            en = (
                "This is a bound task execution request and you only performed observation-only probing.\n"
                "Do not continue with directory-inventory style `file_operations` checks or read-only document probing on the next step.\n"
                "Advance the task now by calling a real execution tool such as `code_executor` or the task-specific analysis tool.\n"
                "If exactly one critical file still must be inspected, read that specific file once and then execute immediately.\n"
                "Do not end this run with another directory-probing report."
            )
        base = _localized_text(language, zh, en)
        if task_hint:
            return f"{base}\n{task_hint}"
        return base

    def _task_context_upstream_artifact_paths(
        self,
        task_context: Optional[TaskExecutionContext],
    ) -> List[str]:
        if task_context is None:
            return []
        collected: List[str] = []
        seen: set[str] = set()
        for dep in list(getattr(task_context, "dependency_outputs", None) or [])[:6]:
            if not isinstance(dep, dict):
                continue
            raw_paths = dep.get("artifact_paths")
            if not isinstance(raw_paths, list):
                continue
            for raw in raw_paths:
                path = str(raw or "").strip()
                if not path or path in seen or self._is_internal_artifact_path(path):
                    continue
                seen.add(path)
                collected.append(path)
                if len(collected) >= 8:
                    return collected
        return collected

    def _can_force_probe_followthrough_execution(
        self,
        task_context: Optional[TaskExecutionContext],
    ) -> bool:
        if not (
            self._is_execute_task_request()
            and self._has_bound_task_context(task_context)
            and self._explicit_task_override_active(task_context)
            and "code_executor" in self.available_tools
        ):
            return False
        # When upstream artifact paths are available, always allow forced
        # execution.  When they are NOT available but the user explicitly
        # requested this task (explicit_task_override), still allow forced
        # execution — the task instruction alone provides enough context, and
        # the code_executor should incorporate any prerequisite work itself
        # rather than reporting BLOCKED_DEPENDENCY to the user.
        return True

    def _build_forced_probe_followthrough_task(
        self,
        *,
        task_context: Optional[TaskExecutionContext],
        user_query: str,
    ) -> str:
        artifact_paths = self._task_context_upstream_artifact_paths(task_context)
        task_label = "the current bound task"
        task_instruction = str(user_query or "").strip()
        if task_context is not None:
            if task_context.task_id is not None:
                task_label = f"Task {task_context.task_id}"
            if task_context.task_name:
                task_label = f"{task_label} ({task_context.task_name})"
            if task_context.task_instruction:
                task_instruction = str(task_context.task_instruction).strip()

        lines = [
            task_instruction or str(user_query or "").strip() or "Execute the current bound task.",
            "",
            "Execution followthrough requirement:",
            f"- Continue {task_label} now with real execution via code_executor.",
        ]
        if artifact_paths:
            lines.extend(
                [
                    "- Authoritative upstream deliverables are already available; do not stop with BLOCKED_DEPENDENCY before attempting execution.",
                    "- Use the upstream artifact paths below directly instead of browsing more directories or documents.",
                    "- Produce only the current task's outputs.",
                    "",
                    "Authoritative upstream artifact paths:",
                    *[f"- {path}" for path in artifact_paths],
                ]
            )
        else:
            lines.extend(
                [
                    "- Do NOT report BLOCKED_DEPENDENCY. The user explicitly requested this task execution.",
                    "- If upstream data or prerequisite outputs are not directly available, incorporate the necessary preprocessing steps within this execution.",
                    "- Use the task instruction above and any available session context to produce the required outputs.",
                ]
            )
        return "\n".join(lines).strip()

    async def _execute_forced_probe_followthrough(
        self,
        *,
        task_context: Optional[TaskExecutionContext],
        user_query: str,
        iteration: int,
        probe_only_execution_cycles: int,
    ) -> Dict[str, Any]:
        forced_task = self._build_forced_probe_followthrough_task(
            task_context=task_context,
            user_query=user_query,
        )
        logger.warning(
            "[DEEP_THINK_NATIVE] Forcing code_executor after probe-only cycles=%s task_id=%s",
            probe_only_execution_cycles,
            getattr(task_context, "task_id", None),
        )
        forced_call = SimpleNamespace(
            name="code_executor",
            id=f"forced_probe_followthrough_{iteration}_{probe_only_execution_cycles}",
            arguments={"task": forced_task},
        )
        return await self._execute_native_tool_call(
            tc=forced_call,
            iteration=iteration,
            index=9999,
        )

    def _build_post_execution_summary_nudge(
        self,
        *,
        task_context: Optional[TaskExecutionContext],
        user_query: str,
        stage: int = 1,
    ) -> str:
        language = detect_reasoning_language(user_query)
        task_bits: List[str] = []
        if task_context:
            if task_context.task_id is not None:
                task_bits.append(f"Task ID={task_context.task_id}")
            if task_context.task_name:
                task_bits.append(f"Task Name={task_context.task_name}")
        task_hint = "\n".join(task_bits)
        can_interpret = any(
            str(tool).strip().lower() == "result_interpreter" for tool in self.available_tools
        )
        if stage >= 2:
            zh = (
                "任务代码已经执行过，当前进入了连续第 2 次只读观察。\n"
                "下一步不要再调用 `file_operations`、`document_reader` 或 `plan_operation`。\n"
                "请立即二选一：\n"
                "1. 基于现有结果直接 `submit_final_answer`，总结关键输出、主要文件和结论；\n"
                f"2. {'调用 `result_interpreter` 解释已有结果后立刻提交最终答案；' if can_interpret else '如果确实还缺一条结论，就基于现有证据直接给出最终答案；'}\n"
                "不要继续做目录浏览、文件点读或计划编辑。"
            )
            en = (
                "Task code has already executed and this is the second consecutive read-only post-execution probe.\n"
                "Do not call `file_operations`, `document_reader`, or `plan_operation` again.\n"
                "Your next step must do exactly one of the following:\n"
                "1. Call `submit_final_answer` now with the key outputs, major files, and conclusions.\n"
                f"2. {'Call `result_interpreter` on the existing outputs and then immediately submit the final answer.' if can_interpret else 'Use the evidence already gathered and submit the final answer now.'}\n"
                "Do not continue browsing directories, peeking at files, or editing the plan."
            )
        else:
            zh = (
                "任务代码已经执行过。当前这一步只是只读观察，不能再作为继续探索的理由。\n"
                "下一步请停止目录/文件浏览，直接收尾："
                f"{'优先调用 `result_interpreter` 解释已有结果，然后立刻 `submit_final_answer`。' if can_interpret else '直接 `submit_final_answer`，总结主要输出和结论。'}\n"
                "不要再调用 `plan_operation`。"
            )
            en = (
                "Task code has already executed. This step is only read-only observation and should not start another exploration loop.\n"
                f"On the next step, stop browsing files and wrap up directly: {'use `result_interpreter` on the existing outputs, then immediately `submit_final_answer`.' if can_interpret else 'call `submit_final_answer` now with the main outputs and conclusions.'}\n"
                "Do not call `plan_operation` again."
            )
        base = _localized_text(language, zh, en)
        if task_hint:
            return f"{base}\n{task_hint}"
        return base

    def _build_task_handoff_execution_nudge(
        self,
        *,
        task_context: Optional[TaskExecutionContext],
        user_query: str,
        previous_task_id: int,
        next_task_id: int,
    ) -> str:
        language = detect_reasoning_language(user_query)
        task_bits: List[str] = [f"Task ID={next_task_id}"]
        if task_context and task_context.task_name:
            task_bits.append(f"Task Name={task_context.task_name}")
        if task_context and task_context.task_instruction:
            task_bits.append(
                f"Task Instruction={self._clip_reference_text(task_context.task_instruction, limit=400)}"
            )
        task_hint = "\n".join(task_bits)
        zh = (
            f"Task {previous_task_id} 已经执行完成，当前绑定任务已自动推进到 Task {next_task_id}。\n"
            "下一步不要继续总结上一个任务，也不要浏览旧目录或旧结果。\n"
            "请立即执行当前绑定任务：优先调用真正的执行工具（如 `code_executor` 或该任务对应的分析工具）。\n"
            "可以使用上一个任务已经生成的现有产物，但不要把请求回退成前一个任务的总结或 BLOCKED_DEPENDENCY。"
        )
        en = (
            f"Task {previous_task_id} has finished and the bound task has automatically advanced to Task {next_task_id}.\n"
            "Do not keep summarizing the previous task or browsing its old directories/results.\n"
            "Execute the current bound task immediately using a real execution tool such as `code_executor` or the task-specific analysis tool.\n"
            "You may use outputs already produced by the previous task, but do not fall back to a summary or BLOCKED_DEPENDENCY for the previous task."
        )
        base = _localized_text(language, zh, en)
        if task_hint:
            return f"{base}\n{task_hint}"
        return base

    def _can_force_handoff_followthrough_execution(
        self,
        task_context: Optional[TaskExecutionContext],
        *,
        next_task_id: Optional[int],
    ) -> bool:
        current_task_id = self._current_bound_task_id(task_context)
        return (
            self._is_execute_task_request()
            and self._has_bound_task_context(task_context)
            and self._explicit_task_override_active(task_context)
            and "code_executor" in self.available_tools
            and next_task_id is not None
            and current_task_id == self._coerce_positive_int(next_task_id)
        )

    def _build_forced_handoff_followthrough_task(
        self,
        *,
        task_context: Optional[TaskExecutionContext],
        user_query: str,
        previous_task_id: int,
        next_task_id: int,
    ) -> str:
        artifact_paths = self._task_context_upstream_artifact_paths(task_context)
        task_label = f"Task {next_task_id}"
        task_instruction = str(user_query or "").strip()
        if task_context is not None:
            if task_context.task_name:
                task_label = f"{task_label} ({task_context.task_name})"
            if task_context.task_instruction:
                task_instruction = str(task_context.task_instruction).strip()

        lines = [
            task_instruction or str(user_query or "").strip() or "Execute the current bound task.",
            "",
            "Task handoff followthrough requirement:",
            f"- Task {previous_task_id} is already completed. Continue {task_label} now with real execution via code_executor.",
            "- The current bound task is already known from plan context; do not ask for task definitions and do not infer blocking from a missing parent task directory name.",
            "- Reuse authoritative upstream outputs directly when relevant instead of falling back to a prose status summary.",
            "- Produce only the current bound task's outputs.",
        ]
        if artifact_paths:
            lines.extend(
                [
                    "",
                    "Authoritative upstream artifact paths:",
                    *[f"- {path}" for path in artifact_paths],
                ]
            )
        return "\n".join(lines).strip()

    async def _execute_forced_handoff_followthrough(
        self,
        *,
        task_context: Optional[TaskExecutionContext],
        user_query: str,
        iteration: int,
        previous_task_id: int,
        next_task_id: int,
        reason: str,
    ) -> Dict[str, Any]:
        forced_task = self._build_forced_handoff_followthrough_task(
            task_context=task_context,
            user_query=user_query,
            previous_task_id=previous_task_id,
            next_task_id=next_task_id,
        )
        logger.warning(
            "[DEEP_THINK_NATIVE] Forcing code_executor after task handoff previous=%s next=%s reason=%s iteration=%s",
            previous_task_id,
            next_task_id,
            reason,
            iteration,
        )
        forced_call = SimpleNamespace(
            name="code_executor",
            id=f"forced_handoff_followthrough_{iteration}_{next_task_id}_{reason}",
            arguments={"task": forced_task},
        )
        return await self._execute_native_tool_call(
            tc=forced_call,
            iteration=iteration,
            index=9998,
        )

    def _build_verified_execution_finalize_nudge(
        self,
        *,
        task_context: Optional[TaskExecutionContext],
        user_query: str,
    ) -> str:
        language = detect_reasoning_language(user_query)
        task_bits: List[str] = []
        if task_context and task_context.task_id is not None:
            task_bits.append(f"Task ID={task_context.task_id}")
        if task_context and task_context.task_name:
            task_bits.append(f"Task Name={task_context.task_name}")
        task_hint = "\n".join(task_bits)
        zh = (
            "当前绑定任务已经执行并验证通过，且当前显式任务链没有剩余待执行任务。\n"
            "下一步不要再调用 `file_operations`、`document_reader`、`plan_operation`、`web_search` 或再次执行代码。\n"
            "如果需要一句总结，可调用 `result_interpreter` 读取现有结果；否则请直接 `submit_final_answer`，总结最终状态、关键输出文件和结论。"
        )
        en = (
            "The current bound task has already executed and passed verification, and there are no remaining tasks in the explicit task chain.\n"
            "Do not call `file_operations`, `document_reader`, `plan_operation`, `web_search`, or run code again.\n"
            "If one short synthesis step is still useful, call `result_interpreter` on the existing outputs; otherwise call `submit_final_answer` now with the final status, key output files, and conclusions."
        )
        base = _localized_text(language, zh, en)
        if task_hint:
            return f"{base}\n{task_hint}"
        return base

    def _should_force_verified_execution_finalization(
        self,
        *,
        task_context: Optional[TaskExecutionContext],
        tool_results: Sequence[Dict[str, Any]],
        had_real_execution_tool: bool = False,
    ) -> bool:
        if not self._is_execute_task_request() or not self._has_bound_task_context(task_context):
            return False
        request_task_id = self._coerce_positive_int(self.request_profile.get("current_task_id"))
        context_task_id = (
            self._coerce_positive_int(task_context.task_id)
            if task_context and task_context.task_id is not None
            else None
        )
        if (
            request_task_id is not None
            and context_task_id is not None
            and request_task_id != context_task_id
            and self._explicit_task_override_active(task_context)
        ):
            return False
        if self._pending_scope_task_ids():
            return False
        if self._tool_results_indicate_verified_success(tool_results):
            if (
                not had_real_execution_tool
                and self._is_verification_only_tool_result_cycle(tool_results)
            ):
                return False
            return True
        if self._collect_task_scoped_output_refs_from_tool_results(
            tool_results,
            task_context=task_context,
        ):
            return True
        if (
            bool(self.request_profile.get("explicit_task_override"))
            and any(self._tool_counts_as_real_execution(item) for item in tool_results)
        ):
            return bool(self._collect_output_refs_from_tool_results(tool_results))
        return False

    def _build_post_execution_probe_stop_answer(
        self,
        *,
        task_context: Optional[TaskExecutionContext],
        user_query: str,
        steps: Sequence[ThinkingStep],
        tool_results: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> str:
        language = detect_reasoning_language(user_query)
        observed_outputs: List[str] = []
        seen: set[str] = set()
        for step in reversed(list(steps)):
            for evidence in reversed(step.evidence or []):
                evidence_type = str((evidence or {}).get("type") or "").strip().lower()
                if evidence_type != "file":
                    continue
                ref = str((evidence or {}).get("ref") or "").strip()
                if (
                    not ref
                    or ref in seen
                    or not self._is_task_scoped_output_ref(ref, task_context)
                ):
                    continue
                seen.add(ref)
                observed_outputs.append(ref)
                if len(observed_outputs) >= 6:
                    break
            if len(observed_outputs) >= 6:
                break

        for ref in self._collect_task_scoped_output_refs_from_tool_results(
            tool_results or [],
            task_context=task_context,
        ):
            if ref in seen:
                continue
            seen.add(ref)
            observed_outputs.append(ref)
            if len(observed_outputs) >= 6:
                break

        task_label = ""
        if task_context and task_context.task_id is not None:
            task_label = f"Task {task_context.task_id}"
            if task_context.task_name:
                task_label += f" ({task_context.task_name})"
        elif task_context and task_context.task_name:
            task_label = task_context.task_name

        verified_success = self._tool_results_indicate_verified_success(tool_results or [])
        if verified_success and not observed_outputs:
            for ref in self._collect_verified_output_refs_from_tool_results(tool_results or []):
                if ref in seen:
                    continue
                seen.add(ref)
                observed_outputs.append(ref)
                if len(observed_outputs) >= 6:
                    break

        if language == "zh":
            lines = []
            if verified_success:
                if task_label:
                    lines.append(f"{task_label} 的代码执行与验证实际上已完成，但模型在收尾总结阶段陷入重复只读观察，已自动停止继续探查。")
                else:
                    lines.append("任务代码执行与验证实际上已完成，但模型在收尾总结阶段陷入重复只读观察，已自动停止继续探查。")
            elif task_label:
                lines.append(f"{task_label} 的代码执行已完成，但后续验证陷入重复观察循环，已自动停止继续探查。")
            else:
                lines.append("任务代码执行已完成，但后续验证陷入重复观察循环，已自动停止继续探查。")
            if observed_outputs:
                lines.append("已确认的稳定输出包括：" if verified_success else "最近已观察到的输出包括：")
                lines.extend(f"- {item}" for item in observed_outputs)
            elif task_label:
                lines.append("未在当前 task 作用域内观察到稳定输出文件。")
            if verified_success:
                lines.append("可直接基于这些结果继续后续任务，或重新发起一次“仅总结结果”的请求。")
            else:
                lines.append("请基于这些已有输出继续查看结果，或重新发起一次“仅总结结果”的请求。")
            return "\n".join(lines)

        lines = []
        if verified_success:
            if task_label:
                lines.append(f"{task_label} actually finished execution and verification, but the model got stuck in repeated read-only post-execution summarization and was stopped automatically.")
            else:
                lines.append("Task execution and verification actually completed, but the model got stuck in repeated read-only post-execution summarization and was stopped automatically.")
        elif task_label:
            lines.append(f"{task_label} finished execution, but post-execution verification entered a repeated observation loop and was stopped automatically.")
        else:
            lines.append("Task code finished execution, but post-execution verification entered a repeated observation loop and was stopped automatically.")
        if observed_outputs:
            lines.append("Confirmed stable outputs include:" if verified_success else "Recently observed outputs include:")
            lines.extend(f"- {item}" for item in observed_outputs)
        elif task_label:
            lines.append("No stable outputs were observed inside the current task scope.")
        if verified_success:
            lines.append("Use these outputs directly for the next task, or retry with a summary-only follow-up.")
        else:
            lines.append("Use these outputs directly, or retry with a summary-only follow-up.")
        return "\n".join(lines)

    def _build_blocked_dependency_answer(
        self,
        *,
        task_context: Optional[TaskExecutionContext],
        user_query: str,
        tool_results: List[Dict[str, Any]],
    ) -> str:
        language = detect_reasoning_language(user_query)
        task_label = ""
        if task_context:
            if task_context.task_name and task_context.task_id is not None:
                task_label = f"Task {task_context.task_id} ({task_context.task_name})"
            elif task_context.task_name:
                task_label = task_context.task_name
            elif task_context.task_id is not None:
                task_label = f"Task {task_context.task_id}"

        clues: List[str] = []
        for item in tool_results:
            clue = self._extract_blocked_dependency_clue(item)
            if clue:
                clues.append(clue)
            if len(clues) >= 2:
                break
        clue_text = "\n".join(f"- {self._clip_reference_text(clue, limit=220)}" for clue in clues[:2])

        zh_header = "BLOCKED_DEPENDENCY: 当前绑定任务缺少继续执行所需的上游交付物或关键输入。"
        en_header = "BLOCKED_DEPENDENCY: The bound task is missing required upstream deliverables or key inputs."
        if task_label:
            zh_header = f"BLOCKED_DEPENDENCY: {task_label} 缺少继续执行所需的上游交付物或关键输入。"
            en_header = f"BLOCKED_DEPENDENCY: {task_label} is missing required upstream deliverables or key inputs."

        zh = (
            f"{zh_header}\n"
            "我已经停止继续做只读探查，因为继续浏览目录/文档不会推进这个 task。\n"
            "按照当前执行边界，本轮不会把它自动改写成前序任务或全量预处理。请先补齐依赖产物，或在任务说明里明确授权补跑前置步骤后再继续。"
        )
        en = (
            f"{en_header}\n"
            "I stopped the repeated read-only probing because more directory or document browsing will not advance this task.\n"
            "Under the current execution boundary, this run will not silently rewrite the task into an upstream preprocessing step. Provide the missing prerequisite outputs, or explicitly authorize backfilling the prerequisite work in the task instruction before retrying."
        )
        base = _localized_text(language, zh, en)
        if clue_text:
            return f"{base}\nObserved clues:\n{clue_text}"
        return base

    @classmethod
    def _extract_blocked_dependency_clue(cls, item: Dict[str, Any]) -> str:
        payload = item.get("tool_result")
        clue = cls._summarize_tool_payload_for_clue(payload)
        if clue:
            return clue

        result_text = str(item.get("tool_result_text") or "").strip()
        if not result_text:
            return ""
        try:
            parsed = json.loads(result_text)
        except Exception:
            parsed = None
        clue = cls._summarize_tool_payload_for_clue(parsed)
        if clue:
            return clue

        stripped = result_text.lstrip()
        if stripped.startswith("{") or stripped.startswith("["):
            return ""
        return result_text

    @classmethod
    def _extract_tool_result_payload(cls, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        payload = item.get("tool_result")
        if isinstance(payload, dict):
            return payload

        raw_text = item.get("tool_result_text")
        if not isinstance(raw_text, str) or not raw_text.strip():
            return None

        try:
            parsed = json.loads(raw_text)
        except (json.JSONDecodeError, TypeError):
            return None

        if isinstance(parsed, dict) and isinstance(parsed.get("result"), dict):
            return parsed["result"]
        return parsed if isinstance(parsed, dict) else None

    @classmethod
    def _tool_counts_as_real_execution(cls, item: Dict[str, Any]) -> bool:
        tool_name = str(item.get("tool_name") or "").strip().lower()
        if tool_name not in cls._CODE_EXECUTION_TOOLS:
            return False
        if tool_name != "terminal_session":
            return True

        payload = cls._extract_tool_result_payload(item)
        if not isinstance(payload, dict):
            return False

        params = item.get("tool_params")
        operation = str(
            payload.get("operation")
            or (params.get("operation") if isinstance(params, dict) else "")
            or ""
        ).strip().lower()
        if operation != "write":
            return False

        verification_state = str(payload.get("verification_state") or "").strip().lower()
        return verification_state == "verified_success"

    @classmethod
    def _iter_tool_payload_dicts(cls, payload: Any) -> Iterable[Dict[str, Any]]:
        current = payload
        visited: set[int] = set()
        while isinstance(current, dict) and id(current) not in visited:
            visited.add(id(current))
            yield current
            nested = current.get("result")
            if not isinstance(nested, dict):
                break
            current = nested

    @classmethod
    def _payload_dict_indicates_verified_success(cls, candidate: Dict[str, Any]) -> bool:
        verification_state = str(candidate.get("verification_state") or "").strip().lower()
        if verification_state == "verified_success":
            return True
        verification_status = str(candidate.get("verification_status") or "").strip().lower()
        if verification_status == "passed":
            return True
        metadata = candidate.get("metadata")
        if isinstance(metadata, dict):
            metadata_verification_status = str(
                metadata.get("verification_status") or ""
            ).strip().lower()
            if metadata_verification_status == "passed":
                return True
            verification = metadata.get("verification")
            if isinstance(verification, dict):
                verification_status = str(verification.get("status") or "").strip().lower()
                if verification_status == "passed":
                    return True
        return False

    @classmethod
    def _tool_results_indicate_verified_success(cls, tool_results: Sequence[Dict[str, Any]]) -> bool:
        for item in tool_results:
            payload = item.get("tool_result")
            for candidate in cls._iter_tool_payload_dicts(payload):
                if cls._payload_dict_indicates_verified_success(candidate):
                    return True
        return False

    @classmethod
    def _collect_verified_output_refs_from_tool_results(
        cls,
        tool_results: Sequence[Dict[str, Any]],
    ) -> List[str]:
        collected: List[str] = []
        seen: set[str] = set()
        for item in tool_results:
            payload = item.get("tool_result")
            for candidate in cls._iter_tool_payload_dicts(payload):
                if not cls._payload_dict_indicates_verified_success(candidate):
                    continue
                artifact_verification = candidate.get("artifact_verification")
                if not isinstance(artifact_verification, dict):
                    continue
                raw_outputs = artifact_verification.get("verified_outputs")
                if not isinstance(raw_outputs, list) or not raw_outputs:
                    raw_outputs = artifact_verification.get("actual_outputs")
                if not isinstance(raw_outputs, list) or not raw_outputs:
                    raw_outputs = artifact_verification.get("expected_deliverables")
                if not isinstance(raw_outputs, list) or not raw_outputs:
                    continue

                base_dir_value = (
                    candidate.get("task_directory_full")
                    or candidate.get("run_directory")
                    or candidate.get("working_directory")
                )
                base_dir = (
                    Path(str(base_dir_value).strip()).expanduser()
                    if str(base_dir_value or "").strip()
                    else None
                )

                for raw in raw_outputs:
                    label = str(raw or "").strip().replace("\\", "/")
                    if not label:
                        continue
                    path = Path(label).expanduser()
                    if not path.is_absolute():
                        if base_dir is None:
                            continue
                        path = base_dir / path
                    try:
                        resolved = path.resolve(strict=False)
                    except Exception:
                        resolved = path
                    normalized = "/" + str(resolved).replace("\\", "/").lstrip("/")
                    if (
                        not normalized
                        or normalized in seen
                        or cls._is_internal_artifact_path(normalized)
                    ):
                        continue
                    seen.add(normalized)
                    collected.append(normalized)
                    if len(collected) >= 8:
                        return collected
        return collected

    @classmethod
    def _collect_task_scoped_output_refs_from_tool_results(
        cls,
        tool_results: Sequence[Dict[str, Any]],
        *,
        task_context: Optional[TaskExecutionContext],
    ) -> List[str]:
        collected: List[str] = []
        seen: set[str] = set()
        for item in tool_results:
            payload = item.get("tool_result")
            for candidate in cls._iter_tool_payload_dicts(payload):
                for key in ("artifact_paths", "session_artifact_paths", "produced_files"):
                    values = candidate.get(key)
                    if not isinstance(values, list):
                        continue
                    for raw in values:
                        ref = str(raw or "").strip()
                        if (
                            not ref
                            or ref in seen
                            or cls._is_internal_artifact_path(ref)
                            or not cls._is_task_scoped_output_ref(ref, task_context)
                        ):
                            continue
                        seen.add(ref)
                        collected.append(ref)
                        if len(collected) >= 8:
                            return collected
        return collected

    @classmethod
    def _collect_output_refs_from_tool_results(
        cls,
        tool_results: Sequence[Dict[str, Any]],
    ) -> List[str]:
        collected: List[str] = []
        seen: set[str] = set()
        for item in tool_results:
            payload = item.get("tool_result")
            for candidate in cls._iter_tool_payload_dicts(payload):
                for key in ("artifact_paths", "session_artifact_paths", "produced_files"):
                    values = candidate.get(key)
                    if not isinstance(values, list):
                        continue
                    for raw in values:
                        ref = str(raw or "").strip()
                        normalized = "/" + ref.replace("\\", "/").lstrip("/") if ref else ""
                        if (
                            not normalized
                            or normalized in seen
                            or cls._is_internal_artifact_path(normalized)
                        ):
                            continue
                        seen.add(normalized)
                        collected.append(normalized)
                        if len(collected) >= 8:
                            return collected
        return collected

    @classmethod
    def _is_task_scoped_output_ref(
        cls,
        ref: str,
        task_context: Optional[TaskExecutionContext],
    ) -> bool:
        normalized = "/" + str(ref or "").strip().replace("\\", "/").lstrip("/")
        if not normalized or normalized == "/" or cls._is_internal_artifact_path(normalized):
            return False
        if not task_context or task_context.task_id is None:
            return True
        try:
            task_id = int(task_context.task_id)
        except (TypeError, ValueError):
            return True
        return bool(
            re.search(
                rf"/(?:results/)?plan\d+_task{task_id}(?:/|$)",
                normalized,
                re.IGNORECASE,
            )
        )

    @classmethod
    def _summarize_tool_payload_for_clue(cls, payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""

        for key in ("summary", "error", "message"):
            value = str(payload.get(key) or "").strip()
            if value:
                return value

        nested = payload.get("result")
        if isinstance(nested, dict):
            nested_summary = cls._summarize_tool_payload_for_clue(nested)
            if nested_summary:
                return nested_summary

        return ""

    @staticmethod
    def _looks_like_blocked_dependency_answer(text: str) -> bool:
        lowered = str(text or "").strip().lower()
        if not lowered:
            return False
        return any(
            token in lowered
            for token in (
                "blocked_dependency",
                "blocked dependency",
                "missing upstream",
                "missing prerequisite",
                "cannot proceed",
                "依赖缺失",
                "前置条件不满足",
                "当前 task 不能继续",
                "阻塞",
            )
        )

    @staticmethod
    def _looks_like_missing_task_definition_answer(text: str) -> bool:
        lowered = str(text or "").strip().lower()
        if not lowered:
            return False
        if "plan68_task" in lowered and any(
            token in lowered
            for token in ("未见到", "未看到", "没有", "missing", "not found", "not see", "not seen")
        ):
            return True
        if any(
            token in lowered
            for token in (
                "task definition",
                "task details",
                "task description",
                "corresponding directory",
                "任务定义",
                "任务描述",
                "具体内容",
                "详细描述",
                "对应的目录",
            )
        ):
            if "task" in lowered or "任务" in lowered:
                return True
        if ("please provide" in lowered or "需要您提供" in lowered or "请提供" in lowered) and (
            "task" in lowered or "任务" in lowered
        ):
            return True
        return False

    def _should_reject_missing_task_definition_answer(
        self,
        text: str,
        *,
        task_context: Optional[TaskExecutionContext],
    ) -> bool:
        return (
            self._is_execute_task_request()
            and self._has_bound_task_context(task_context)
            and self._explicit_task_override_active(task_context)
            and self._looks_like_missing_task_definition_answer(text)
        )

    def _is_valid_final_answer(self, text: str, *, user_query: str) -> bool:
        cleaned = sanitize_professional_response_text(str(text or "").strip())
        if len(cleaned) < 4:
            return False
        return not is_process_only_answer(cleaned, user_query=user_query)

    def _should_retry_external_tool(self, tool_name: str, *, success: bool) -> bool:
        return (tool_name or "").strip().lower() in self.EXTERNAL_RETRIABLE_TOOLS and not success

    @staticmethod
    def _try_parse_json_object(raw: Any) -> Optional[Dict[str, Any]]:
        if isinstance(raw, dict):
            return raw
        text = str(raw or "").strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    @classmethod
    def _extract_outcomes_from_step(cls, step: ThinkingStep) -> List[Dict[str, Any]]:
        outcomes: List[Dict[str, Any]] = []
        for entry in cls._extract_tool_payloads_from_step(step):
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue
            inner = cls._unwrap_tool_result(payload)
            success, error = cls._normalize_tool_callback_outcome(payload)
            outcomes.append(
                {
                    "tool": entry.get("tool"),
                    "success": success,
                    "error": error or payload.get("error"),
                    "summary": payload.get("summary") or inner.get("summary") or inner.get("message"),
                }
            )
        return outcomes

    @classmethod
    def _extract_tool_payloads_from_step(cls, step: ThinkingStep) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        action_payload = cls._try_parse_json_object(step.action)
        tool_names = cls._tool_names_from_payload(action_payload)
        action_result_text = str(step.action_result or "").strip()
        if not tool_names or not action_result_text:
            return entries

        matched_any = False
        for block in [part.strip() for part in action_result_text.split("\n\n") if part.strip()]:
            match = _MULTI_TOOL_RESULT_LINE_RE.match(block)
            if not match:
                continue
            payload = cls._try_parse_json_object(match.group("payload"))
            if not payload:
                continue
            entries.append(
                {
                    "tool": match.group("tool").strip(),
                    "payload": payload,
                }
            )
            matched_any = True

        if matched_any:
            return entries

        payload = cls._try_parse_json_object(action_result_text)
        if payload:
            entries.append(
                {
                    "tool": tool_names[0],
                    "payload": payload,
                }
            )
            return entries

        if len(tool_names) == 1 and action_result_text.lower().startswith("error"):
            entries.append(
                {
                    "tool": tool_names[0],
                    "payload": {
                        "success": False,
                        "error": action_result_text,
                        "summary": action_result_text,
                    },
                }
            )
        return entries

    @classmethod
    def _collect_tool_failures_from_steps(cls, steps: List[ThinkingStep]) -> List[Dict[str, Any]]:
        failures: List[Dict[str, Any]] = []
        for step in steps:
            for outcome in cls._extract_outcomes_from_step(step):
                if outcome.get("success") is False:
                    failures.append(
                        {
                            "tool": str(outcome.get("tool") or "").strip(),
                            "error": str(outcome.get("error") or "").strip(),
                            "summary": str(outcome.get("summary") or "").strip(),
                            "iteration": step.iteration,
                        }
                    )
        return failures

    def _search_verified_from_steps(self, steps: List[ThinkingStep]) -> bool:
        seen_external = False
        successful_external = False
        for step in steps:
            for outcome in self._extract_outcomes_from_step(step):
                tool_name = str(outcome.get("tool") or "").strip().lower()
                if tool_name not in self.EXTERNAL_RETRIABLE_TOOLS:
                    continue
                seen_external = True
                if outcome.get("success") is True:
                    successful_external = True
        return True if not seen_external else successful_external

    def _apply_external_search_notice(
        self,
        answer: str,
        *,
        user_query: str,
        tool_failures: List[Dict[str, Any]],
        search_verified: bool,
    ) -> str:
        text = str(answer or "").strip()
        if not text or search_verified or not self._is_research_or_execute():
            return text

        failed_external = [
            item for item in tool_failures
            if str(item.get("tool") or "").strip().lower() in self.EXTERNAL_RETRIABLE_TOOLS
        ]
        if not failed_external:
            return text

        language = detect_reasoning_language(user_query or text)
        tool_names = ", ".join(
            sorted(
                {
                    str(item.get("tool") or "").strip()
                    for item in failed_external
                    if str(item.get("tool") or "").strip()
                }
            )
        ) or "external search"
        notice = _localized_text(
            language,
            f"说明：本轮外部检索未成功完成（{tool_names} 失败或超时），以下内容基于当前会话上下文和已有稳定知识整理，未经过本轮在线检索验证。建议稍后重试检索，或手动补充 PubMed / 网页来源后再核对。",
            f"Note: External retrieval did not complete successfully in this run ({tool_names} failed or timed out). The response below is based on the current session context and stable prior knowledge, and was not verified by live search during this run. Consider retrying later or checking PubMed / web sources manually.",
        )
        normalized_notice = sanitize_professional_response_text(notice)
        if text.startswith(normalized_notice):
            return text
        return f"{normalized_notice}\n\n{text}"

    @classmethod
    def _collect_execute_truth_events(
        cls,
        steps: Sequence[ThinkingStep],
    ) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        order = 0
        for step in steps:
            for entry in cls._extract_tool_payloads_from_step(step):
                tool_name = str(entry.get("tool") or "").strip().lower()
                payload = entry.get("payload")
                if not isinstance(payload, dict):
                    continue
                inner = cls._unwrap_tool_result(payload)
                if not isinstance(inner, dict):
                    continue

                raw_success = inner.get("success")
                if raw_success is None:
                    raw_success = payload.get("success")
                success = bool(raw_success) if raw_success is not None else False

                operation = str(
                    inner.get("operation")
                    or payload.get("operation")
                    or ""
                ).strip().lower()
                task_type = str(inner.get("task_type") or "").strip().lower()
                execution_status = str(inner.get("execution_status") or "").strip().lower()
                verification_state = str(inner.get("verification_state") or "").strip().lower()

                kind: Optional[str] = None
                trusted = False
                if tool_name == "result_interpreter":
                    if operation in {"profile", "metadata"} or (
                        operation == "analyze" and task_type == "text_only"
                    ):
                        kind = "profile"
                        trusted = success
                    elif operation in {"execute", "analyze"}:
                        kind = "execution"
                        trusted = success and (
                            execution_status == "success" or not execution_status
                        )
                elif tool_name == "terminal_session":
                    if operation == "write":
                        kind = "execution"
                        trusted = success and verification_state == "verified_success"
                elif tool_name in cls._CODE_EXECUTION_TOOLS:
                    kind = "execution"
                    trusted = success

                if kind is None:
                    continue

                summary_text = ""
                profile_payload = inner.get("profile")
                if isinstance(profile_payload, dict):
                    profile_summary = profile_payload.get("summary")
                    if isinstance(profile_summary, str) and profile_summary.strip():
                        summary_text = profile_summary.strip()
                if not summary_text:
                    execution_output = inner.get("execution_output")
                    if isinstance(execution_output, str) and execution_output.strip():
                        summary_text = execution_output.strip()
                if not summary_text:
                    for key in ("summary", "error", "execution_error", "message"):
                        candidate = str(
                            inner.get(key) or payload.get(key) or ""
                        ).strip()
                        if candidate:
                            summary_text = candidate
                            break

                error_text = str(
                    inner.get("error")
                    or inner.get("execution_error")
                    or payload.get("error")
                    or ""
                ).strip()

                events.append(
                    {
                        "order": order,
                        "iteration": step.iteration,
                        "tool": tool_name,
                        "operation": operation,
                        "kind": kind,
                        "success": success,
                        "trusted": trusted,
                        "summary_text": summary_text,
                        "error": error_text,
                    }
                )
                order += 1
        return events

    def _build_execute_failure_warning(
        self,
        *,
        user_query: str,
        failed_event: Dict[str, Any],
    ) -> str:
        """Soft warning prepended to the model's answer when execution failed
        but the model produced substantive content from read-only tools."""
        language = detect_reasoning_language(user_query or "")
        tool_name = str(failed_event.get("tool") or "execution tool").strip()
        failure_detail = str(
            failed_event.get("error")
            or failed_event.get("summary_text")
            or "unknown failure"
        ).strip()
        return _localized_text(
            language,
            (
                f"> ⚠️ **注意**：本轮主执行工具未成功（{tool_name} 失败：{failure_detail}）。"
                "以下内容基于文件读取工具的输出，统计数值未经代码验证，仅供参考。"
            ),
            (
                f"> ⚠️ **Warning**: The main execution tool failed in this run "
                f"({tool_name}: {failure_detail}). "
                f"The content below is based on file-reading tools; "
                f"statistical figures are not code-verified and should be treated as approximate."
            ),
        )

    def _build_execute_failure_truth_barrier(
        self,
        *,
        user_query: str,
        failed_event: Dict[str, Any],
        profile_text: Optional[str] = None,
    ) -> str:
        language = detect_reasoning_language(user_query or profile_text or "")
        tool_name = str(failed_event.get("tool") or "execution tool").strip()
        failure_detail = str(
            failed_event.get("error")
            or failed_event.get("summary_text")
            or "unknown failure"
        ).strip()

        if profile_text:
            return _localized_text(
                language,
                (
                    f"说明：本轮真正的执行工具未成功完成（{tool_name} 失败：{failure_detail}）。"
                    "下面只保留本轮已验证的确定性数据 profile 结果，不把它当作完整分析已完成：\n\n"
                    f"{profile_text}"
                ),
                (
                    f"Note: The main execution tool did not complete successfully in this run "
                    f"({tool_name} failed: {failure_detail}). The content below is limited to "
                    f"verified deterministic dataset profiling from this run and should not be "
                    f"treated as a completed full analysis.\n\n{profile_text}"
                ),
            )

        return _localized_text(
            language,
            (
                f"本轮真正的执行工具未成功完成（{tool_name} 失败：{failure_detail}）。"
                "因此不能把后续分析性表述视为已验证结论。当前只能确认执行被该错误阻塞；"
                "如需继续，请先修复该失败原因后再重新运行。"
            ),
            (
                f"The main execution tool did not complete successfully in this run "
                f"({tool_name} failed: {failure_detail}). Any later analysis-style narrative "
                f"cannot be treated as verified. At this point the run is blocked by that error; "
                f"fix the failure first and rerun to obtain a trustworthy result."
            ),
        )

    def _apply_execute_failure_truth_barrier(
        self,
        answer: str,
        *,
        user_query: str,
        steps: Sequence[ThinkingStep],
    ) -> str:
        text = str(answer or "").strip()
        if not text or not self._is_execute_task_request():
            return text

        events = self._collect_execute_truth_events(steps)
        failed_execution_events = [
            event
            for event in events
            if event.get("kind") == "execution" and not event.get("success")
        ]
        if not failed_execution_events:
            return text

        last_failure = failed_execution_events[-1]
        last_failure_order = int(last_failure.get("order", -1))
        later_events = [
            event for event in events if int(event.get("order", -1)) > last_failure_order
        ]

        if any(
            event.get("kind") == "execution" and event.get("trusted")
            for event in later_events
        ):
            return text

        profile_recovery = next(
            (
                event
                for event in reversed(later_events)
                if event.get("kind") == "profile"
                and event.get("trusted")
                and str(event.get("summary_text") or "").strip()
            ),
            None,
        )
        if profile_recovery is not None:
            barrier = self._build_execute_failure_truth_barrier(
                user_query=user_query,
                failed_event=last_failure,
                profile_text=str(profile_recovery.get("summary_text") or "").strip(),
            )
            return barrier

        # Check if the model's answer contains substantive content from
        # successful read tools (document_reader, file_operations, etc.).
        # If so, prepend a warning instead of replacing the entire answer,
        # so partial results are preserved for the user.
        has_substantive_answer = len(text) > 200
        if has_substantive_answer:
            if _looks_like_completion_claim_text(text):
                return self._build_execute_failure_truth_barrier(
                    user_query=user_query,
                    failed_event=last_failure,
                )
            warning = self._build_execute_failure_warning(
                user_query=user_query,
                failed_event=last_failure,
            )
            return f"{warning}\n\n---\n\n{text}"

        return self._build_execute_failure_truth_barrier(
            user_query=user_query,
            failed_event=last_failure,
        )

    @staticmethod
    def _unwrap_tool_result(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Return the innermost result dict, handling nested {result: {...}} wrappers."""
        inner = payload.get("result")
        return inner if isinstance(inner, dict) else payload

    @classmethod
    def _collect_plan_operation_events(cls, steps: List[ThinkingStep]) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        for step in steps:
            for entry in cls._extract_tool_payloads_from_step(step):
                if str(entry.get("tool") or "").strip().lower() != "plan_operation":
                    continue
                payload = entry.get("payload")
                if not isinstance(payload, dict):
                    continue
                rp = cls._unwrap_tool_result(payload)
                success = bool(rp.get("success", payload.get("success")))
                operation = str(rp.get("operation") or "").strip().lower()
                plan_id = cls._coerce_positive_int(rp.get("plan_id"))
                plan_title = str(rp.get("plan_title") or rp.get("title") or "").strip()
                error = str(rp.get("error") or "").strip()
                if not error:
                    error = str(
                        payload.get("error")
                        or payload.get("summary")
                        or (rp.get("message") if not success else "")
                        or ""
                    ).strip()
                applied_changes = rp.get("applied_changes")
                failed_changes = rp.get("failed_changes")
                try:
                    applied_changes = int(applied_changes) if applied_changes is not None else None
                except (TypeError, ValueError):
                    applied_changes = None
                try:
                    failed_changes = int(failed_changes) if failed_changes is not None else None
                except (TypeError, ValueError):
                    failed_changes = None
                events.append(
                    {
                        "success": success,
                        "operation": operation or None,
                        "plan_id": plan_id,
                        "plan_title": plan_title or None,
                        "applied_changes": applied_changes,
                        "failed_changes": failed_changes,
                        "error": error or None,
                    }
                )
        return events

    def _summarize_structured_plan_outcome(
        self,
        steps: List[ThinkingStep],
        *,
        user_query: str = "",
    ) -> Dict[str, Any]:
        required = self._requires_structured_plan()
        mode = self._plan_request_mode()
        bound_plan_id = self._current_plan_id()
        bound_plan_title = self._current_plan_title()
        language = detect_reasoning_language(user_query)
        outcome: Dict[str, Any] = {
            "required": required,
            "mode": mode or None,
            "called": False,
            "satisfied": False,
            "state": None,
            "message": None,
            "plan_id": bound_plan_id,
            "plan_title": bound_plan_title,
            "operation": None,
        }
        if not required or not mode:
            return outcome

        events = self._collect_plan_operation_events(steps)
        outcome["called"] = bool(events)

        if mode in {"create", "create_new"}:
            for event in events:
                if event.get("success") and event.get("operation") == "create" and event.get("plan_id") is not None:
                    outcome.update(
                        {
                            "satisfied": True,
                            "state": "created",
                            "message": _localized_text(
                                language,
                                "已成功创建结构化计划。",
                                "Structured plan created successfully.",
                            ),
                            "plan_id": event.get("plan_id"),
                            "plan_title": event.get("plan_title") or bound_plan_title,
                            "operation": "create",
                        }
                    )
                    return outcome

            if events:
                last_error = str(events[-1].get("error") or "").strip()
                outcome.update(
                    {
                        "state": "failed",
                        "message": (
                            _localized_text(
                                language,
                                "结构化计划创建失败。",
                                "Structured plan creation failed.",
                            )
                            + (f" {last_error}" if last_error else "")
                        ).strip(),
                    }
                )
                return outcome

            outcome.update(
                {
                    "state": "text_only",
                    "message": _localized_text(
                        language,
                        "本轮只生成了文本建议，未创建结构化计划。",
                        "This run produced text guidance only; no structured plan was created.",
                    ),
                }
            )
            return outcome

        required_update_ops = self._required_bound_plan_operations()
        if required_update_ops:
            satisfied_events: Dict[str, Dict[str, Any]] = {}
            for event in events:
                if not event.get("success"):
                    continue
                operation = str(event.get("operation") or "").strip().lower()
                if operation == "optimize" and int(event.get("applied_changes") or 0) <= 0:
                    continue
                if operation not in required_update_ops:
                    continue
                event_plan_id = event.get("plan_id") or bound_plan_id
                if (
                    bound_plan_id is not None
                    and event_plan_id is not None
                    and event_plan_id != bound_plan_id
                ):
                    continue
                satisfied_events.setdefault(operation, event)

            missing_ops = [op for op in required_update_ops if op not in satisfied_events]
            if not missing_ops:
                last_required_op = required_update_ops[-1]
                matched_event = satisfied_events[last_required_op]
                success_message = {
                    ("review",): _localized_text(
                        language,
                        "已成功审核当前结构化计划。",
                        "The bound structured plan was reviewed successfully.",
                    ),
                    ("optimize",): _localized_text(
                        language,
                        "已成功优化当前结构化计划。",
                        "The bound structured plan was optimized successfully.",
                    ),
                    ("review", "optimize"): _localized_text(
                        language,
                        "已成功审核并优化当前结构化计划。",
                        "The bound structured plan was reviewed and optimized successfully.",
                    ),
                }.get(
                    tuple(required_update_ops),
                    _localized_text(
                        language,
                        "已成功更新当前结构化计划。",
                        "Structured plan updated successfully.",
                    ),
                )
                outcome.update(
                    {
                        "satisfied": True,
                        "state": "updated",
                        "message": success_message,
                        "plan_id": matched_event.get("plan_id") or bound_plan_id,
                        "plan_title": matched_event.get("plan_title") or bound_plan_title,
                        "operation": last_required_op,
                    }
                )
                return outcome

            missing_text = {
                ("review",): _localized_text(
                    language,
                    "当前结构化计划尚未成功审核。",
                    "The bound structured plan was not reviewed successfully.",
                ),
                ("optimize",): _localized_text(
                    language,
                    "当前结构化计划尚未成功优化。",
                    "The bound structured plan was not optimized successfully.",
                ),
                ("review", "optimize"): _localized_text(
                    language,
                    "当前结构化计划尚未完成审核并优化。",
                    "The bound structured plan was not both reviewed and optimized successfully.",
                ),
            }.get(
                tuple(required_update_ops),
                _localized_text(
                    language,
                    "当前结构化计划未更新成功。",
                    "The bound structured plan was not updated successfully.",
                ),
            )
            if events:
                last_error = str(events[-1].get("error") or "").strip()
                outcome.update(
                    {
                        "state": "failed",
                        "message": (missing_text + (f" {last_error}" if last_error else "")).strip(),
                    }
                )
                return outcome
            outcome.update(
                {
                    "state": "text_only",
                    "message": missing_text,
                }
            )
            return outcome

        allowed_update_ops = {"get", "review", "optimize"}
        for event in events:
            if not event.get("success"):
                continue
            if event.get("operation") not in allowed_update_ops:
                continue
            if event.get("operation") == "optimize" and int(event.get("applied_changes") or 0) <= 0:
                continue
            event_plan_id = event.get("plan_id") or bound_plan_id
            if bound_plan_id is not None and event_plan_id is not None and event_plan_id != bound_plan_id:
                continue
            outcome.update(
                {
                    "satisfied": True,
                    "state": "updated",
                    "message": _localized_text(
                        language,
                        "已成功更新当前结构化计划。",
                        "Structured plan updated successfully.",
                    ),
                    "plan_id": event_plan_id,
                    "plan_title": event.get("plan_title") or bound_plan_title,
                    "operation": event.get("operation"),
                }
            )
            return outcome

        if events:
            last_error = str(events[-1].get("error") or "").strip()
            outcome.update(
                {
                    "state": "failed",
                    "message": (
                        _localized_text(
                            language,
                            "当前结构化计划未更新成功。",
                            "The bound structured plan was not updated successfully.",
                        )
                        + (f" {last_error}" if last_error else "")
                    ).strip(),
                }
            )
            return outcome

        outcome.update(
            {
                "state": "text_only",
                "message": _localized_text(
                    language,
                    "本轮只生成了文本建议，未更新当前结构化计划。",
                    "This run produced text guidance only; the bound structured plan was not updated.",
                ),
            }
        )
        return outcome

    def _build_structured_plan_requirement_block(self) -> str:
        if not self._requires_structured_plan():
            return ""
        mode = self._plan_request_mode()
        current_plan_id = self._current_plan_id()
        required_update_ops = self._required_bound_plan_operations()
        lines = [
            "=== STRUCTURED PLAN REQUIREMENT ===",
            "- The user explicitly asked for a real structured plan. A prose-only answer does NOT satisfy this request.",
        ]
        if mode in {"create", "create_new"}:
            lines.append(
                "- Before submit_final_answer, you must successfully call plan_operation with operation='create' and obtain a real plan_id."
            )
        elif mode == "update_bound":
            if required_update_ops == ["review"]:
                lines.append(
                    "- Before submit_final_answer, you must successfully call plan_operation with operation='review' on the currently bound plan."
                )
            elif required_update_ops == ["optimize"]:
                lines.append(
                    "- Before submit_final_answer, you must successfully call plan_operation with operation='optimize' on the currently bound plan and apply real plan changes."
                )
            elif required_update_ops == ["review", "optimize"]:
                lines.append(
                    "- Before submit_final_answer, you must successfully call plan_operation with operation='review' on the currently bound plan, then call operation='optimize' to apply real changes."
                )
            else:
                lines.append(
                    "- Before submit_final_answer, you must successfully call plan_operation on the currently bound plan using get/review/optimize."
                )
            if current_plan_id is not None:
                lines.append(f"- The bound plan_id is {current_plan_id}. Do not create a new plan unless the user explicitly asked for a new one.")
                lines.append(f"- IMPORTANT: Only use plan_id={current_plan_id} in plan_operation calls. Other plan IDs will be rejected.")
                lines.append("- If the chat history mentions other plans from previous conversations, ignore them — focus only on the currently bound plan.")
        lines.append(
            "- If plan_operation fails or is never called, your final answer must clearly state that no structured plan was created or updated."
        )
        return "\n".join(lines) + "\n"

    @classmethod
    def _extract_successful_created_plan_from_tool_results(
        cls,
        tool_results: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        for item in tool_results:
            if str(item.get("tool_name") or "").strip().lower() != "plan_operation":
                continue
            payload = item.get("tool_result")
            if not isinstance(payload, dict):
                continue
            inner = cls._unwrap_tool_result(payload)
            success = bool(inner.get("success", payload.get("success")))
            operation = str(inner.get("operation") or payload.get("operation") or "").strip().lower()
            if not success or operation != "create":
                continue
            plan_id = cls._coerce_positive_int(inner.get("plan_id") or payload.get("plan_id"))
            if plan_id is None:
                continue
            plan_title = str(
                inner.get("plan_title")
                or inner.get("title")
                or payload.get("plan_title")
                or payload.get("title")
                or ""
            ).strip()
            return {
                "plan_id": plan_id,
                "plan_title": plan_title or None,
                "already_bound_plan_reused": bool(
                    inner.get("already_bound_plan_reused")
                    or payload.get("already_bound_plan_reused")
                ),
            }
        return None

    def _build_created_plan_finalize_nudge(
        self,
        *,
        user_query: str,
        plan_id: int,
        plan_title: Optional[str] = None,
    ) -> str:
        language = detect_reasoning_language(user_query)
        title_suffix = f"，标题：{plan_title}" if language == "zh" and plan_title else (
            f", title: {plan_title}" if plan_title else ""
        )
        return _localized_text(
            language,
            (
                f"结构化计划已创建成功（plan_id={plan_id}{title_suffix}）。"
                "不要再次调用 `plan_operation` 的 `create`。"
                "请基于这个已创建的计划，简要说明核心目标和任务结构，"
                "然后立刻调用 `submit_final_answer` 结束本轮。"
            ),
            (
                f"The structured plan has already been created successfully (plan_id={plan_id}{title_suffix}). "
                "Do not call `plan_operation` with `create` again. "
                "Briefly summarize the created plan's goal and task structure, then call "
                "`submit_final_answer` immediately to finish this turn."
            ),
        )

    def _get_structured_plan_retry_prompt(self) -> str:
        mode = self._plan_request_mode()
        current_plan_id = self._current_plan_id()
        required_update_ops = self._required_bound_plan_operations()
        if mode in {"create", "create_new"}:
            return (
                "This request requires a real structured plan. You have not successfully created one yet. "
                "Call plan_operation with operation='create' now. Do not finish with submit_final_answer until create succeeds "
                "or you need to clearly report that structured plan creation failed."
            )
        if current_plan_id is not None:
            if required_update_ops == ["review"]:
                return (
                    f"This request requires reviewing the bound structured plan (plan_id={current_plan_id}). "
                    "Call plan_operation with operation='review' now. Do not finish with submit_final_answer until review succeeds "
                    "or you need to clearly report that the structured plan was not reviewed."
                )
            if required_update_ops == ["optimize"]:
                return (
                    f"This request requires optimizing the bound structured plan (plan_id={current_plan_id}). "
                    "Call plan_operation with operation='optimize' now and apply real plan changes. Do not finish with submit_final_answer "
                    "until optimize succeeds or you need to clearly report that the structured plan was not optimized."
                )
            if required_update_ops == ["review", "optimize"]:
                return (
                    f"This request requires reviewing and then optimizing the bound structured plan (plan_id={current_plan_id}). "
                    "Call plan_operation with operation='review' first, then call operation='optimize' with concrete changes. "
                    "Do not finish with submit_final_answer until both succeed or you need to clearly report that review/optimization did not complete."
                )
            return (
                f"This request requires updating the bound structured plan (plan_id={current_plan_id}). "
                "Call plan_operation with get/review/optimize on that plan now. Do not finish with submit_final_answer until one succeeds "
                "or you need to clearly report that the structured plan was not updated."
            )
        return (
            "This request requires a real structured plan action. Use plan_operation now before submit_final_answer."
        )

    def _ensure_structured_plan_notice(
        self,
        answer: str,
        *,
        outcome: Dict[str, Any],
        user_query: str,
    ) -> str:
        text = str(answer or "").strip()
        if not outcome.get("required") or outcome.get("satisfied"):
            return text
        notice = sanitize_professional_response_text(str(outcome.get("message") or "").strip())
        if not notice:
            notice = _localized_text(
                detect_reasoning_language(user_query or text),
                "本轮未创建或更新结构化计划。",
                "A structured plan was not created or updated in this run.",
            )
        if not text:
            return notice
        if text.startswith(notice):
            return text
        return f"{notice}\n\n{text}"

    def _build_request_tier_block(self) -> str:
        tier = self._request_tier()
        intent_type = str(self.request_profile.get("intent_type") or "").strip().lower()
        local_inspect_note = (
            "- For local inspection requests asking what's inside, schema, columns, previews, or a quick dataset overview, start with file_operations, document_reader, or result_interpreter metadata/profile and keep the path lightweight.\n"
            "- Prefer result_interpreter profile for deterministic row/column counts, sample values, and simple ID-overlap checks on local CSV/TSV-style datasets.\n"
            "- Do not jump to result_interpreter analyze or code_executor unless the user clearly needs calculations, transformations, or plots.\n"
            if intent_type == "local_inspect"
            else ""
        )
        if tier == "light":
            return (
                "=== REQUEST TIER: LIGHT ===\n"
                "- Answer directly and briefly.\n"
                "- Use the smallest amount of explanation that fully answers the user.\n"
                "- Keep the tone professional and plain; avoid decorative emojis or hype.\n"
                "- Prefer finishing in one short reasoning pass.\n"
                + local_inspect_note
                + "- Use tools when the answer depends on file/workspace/remote state; "
                "a no-tool guess is not an acceptable substitute for a check you could run.\n"
            )
        if tier == "standard":
            return (
                "=== REQUEST TIER: STANDARD ===\n"
                "- Give a concise but complete direct answer.\n"
                "- Avoid research style output unless the user explicitly asks for sources or latest information.\n"
                "- Keep the tone professional and plain; avoid decorative emojis or hype.\n"
                "- Prefer low-overhead execution, but do not ignore required evidence.\n"
                + local_inspect_note
                + "- Prioritize tool-backed facts over stylistic completeness.\n"
            )
        if tier == "research":
            return (
                "=== REQUEST TIER: RESEARCH ===\n"
                "- Use targeted evidence gathering when it improves correctness.\n"
                "- Cite verifiable sources for time-sensitive or factual claims.\n"
                "- Keep the writing professional and restrained; avoid decorative emojis in headings or labels.\n"
                "- Keep research focused on the exact user question; avoid unrelated survey padding.\n"
            )
        if tier == "execute":
            execute_focus_note = ""
            if self._is_brief_execute_followup():
                execute_focus_note = (
                    "- This is a short execution follow-up: focus the final answer on the current task outcome.\n"
                    "- Do not recap prior project milestones, older test rounds, or historical status tables unless the user explicitly asks.\n"
                    "- Do not append next-step menus or optional directions unless the user asks what to do next.\n"
                    "- If continuation context already identifies the target file, path, task, or blocker, continue from that anchor instead of restarting broad workspace discovery.\n"
                )
            return (
                "=== REQUEST TIER: EXECUTE ===\n"
                "- Prioritize finishing the requested task over broad background research.\n"
                "- Use file/code/task tools as needed.\n"
                "- Keep the tone professional and execution-focused; avoid decorative emojis or cheerleading.\n"
                "- Use web_search only to fill a concrete factual gap that blocks execution quality.\n"
                "- For a bound execute_task request, observation-only probing is only a short precursor. After one observation-only cycle, move to real execution or report BLOCKED_DEPENDENCY.\n"
                "- For local structured-data overview/schema/count/sample-value requests, prefer result_interpreter profile before heavier code_executor runs.\n"
                "- Do not silently rewrite the current task into an upstream preprocessing task just because prerequisite deliverables are missing.\n"
                "- For immutable source inputs, prefer canonical data-directory paths over same-named session-root `results/` copies, especially when the session copy is empty or malformed.\n"
                "- For single-cell integration tasks, fewer than 2 valid upstream samples means the preconditions are not met; do not claim integration succeeded.\n"
                + execute_focus_note
            )
        return ""

    def _build_capability_floor_block(self) -> str:
        intent_type = str(self.request_profile.get("intent_type") or "").strip().lower()
        lines = [
            "=== TOOL ACCESS ===",
            "- You have access to ALL registered tools. Choose the right tool based on user intent.",
            "- For simple chat questions, you may answer directly without tools.",
            "- For questions involving data, files, remote services, or verifiable facts, use tools to obtain ground truth — never fabricate results.",
            "- If a tool call fails, report the failure honestly; do not invent tool-like certainty.",
        ]
        current_plan_id = self._current_plan_id()
        if current_plan_id is not None:
            lines.append(
                f"- This session is bound to plan {current_plan_id}. "
                "When calling plan_operation, ALWAYS use this plan_id. "
                "Ignore references to other plans in the chat history."
            )
        if intent_type == "local_mutation":
            lines.append(
                "- This request is a local filesystem mutation. Do the change directly instead of telling the user to run commands manually."
            )
            lines.append(
                "- Prefer terminal_session for unzip/extract/move/rename/delete style operations."
            )
        return "\n".join(lines) + "\n"

    def _build_grounded_tooling_block(self) -> str:
        """Nudge the agent to use tools for verification when facts are checkable."""
        return (
            "=== GROUNDED TOOLING ===\n"
            "- If the answer depends on local files, workspace contents, remote task/API state, sequences, or "
            "other checkable facts, use the appropriate tool(s) before stating specifics.\n"
            "- Do not substitute confident-sounding prose for evidence when a tool can obtain the "
            "ground truth within reasonable latency.\n\n"
        )

    def _build_shared_strategy_block(self) -> str:
        return (
            "=== EFFORT AND TOOLING POLICY ===\n"
            "1. First classify the request: casual chat, direct answer, evidence-backed research, or execution task.\n"
            "2. Match effort to the request. Default to the lightest path that fully satisfies the user.\n"
            "3. Do NOT start broad web/literature research for greetings, casual follow-ups, simple explanations, or opinion-style questions.\n"
            "4. Use tools when they materially improve correctness or usefulness: latest information, explicit citations, factual verification, file/workspace actions, or complex analysis. "
            "Prefer tool-backed checks for anything that depends on real files, remote services, or run results (unless the user clearly wants opinion-only).\n"
            "5. One precise tool call is better than several redundant calls.\n"
            "6. Conclude as soon as the user's need is satisfied; do not pad the reply with extra background, market analysis, or references unless they help answer the request.\n"
            "7. If the user asks for depth, latest research, or sources, then increase rigor and evidence gathering.\n\n"
            "=== WRITING STYLE ===\n"
            f"- {PROFESSIONAL_STYLE_INSTRUCTION}\n"
            "- Prefer clear headings and plain wording over expressive decoration.\n\n"
            "=== TOOL PRIORITY ===\n"
            "- For accession-based FASTA downloads, call sequence_fetch first.\n"
            "- For FASTA/FASTQ/sequence work, ALWAYS try bio_tools first before code_executor.\n"
            "- If the user provides inline sequence text (not a file), pass it as bio_tools(sequence_text=...).\n"
            "- If bio_tools routing is uncertain, call bio_tools(operation='help') first; use web_search only when help is insufficient.\n"
            "- For complex custom analysis not covered by bio_tools, then use code_executor.\n"
            "- Never use code_executor as fallback for sequence_fetch failures.\n"
            "- Never use code_executor as fallback for bio_tools input-conversion/parsing failures.\n"
            "- For status polling tools, if state is unchanged across several checks, stop active polling and summarize current status.\n"
            "- If the user explicitly asks for a plan or task breakdown and plan_operation is available, use plan_operation to create or update a structured plan instead of replying with a prose-only pseudo-plan.\n"
            "- For plan creation, research is optional. Use web_search first only when latest evidence or external best practices materially affect the plan; otherwise create or update the plan directly from current context.\n"
            "- When executing a currently bound plan task, do NOT use plan_operation or task_operation just to mark that task completed/failed. Tool execution auto-sync already handles current task status; use plan_operation/task_operation only for structural plan edits.\n"
            "- When executing a currently bound plan task, observation-only tools (read-only file_operations, document_reader, vision_reader) may clarify one concrete uncertainty, but they are not task completion. Do not loop on probe-only exploration.\n"
            "- If the current bound task depends on upstream deliverables that are missing, report BLOCKED_DEPENDENCY clearly instead of silently switching to a different upstream task.\n"
            "- Do not convert an integration/analysis task into full upstream preprocessing unless the task instruction explicitly authorizes backfilling prerequisites.\n"
            "- For immutable source inputs, prefer canonical data-directory paths over same-named session-root `results/` copies; ignore empty or malformed session duplicates.\n"
            "- For single-cell workflows, do not assume `adata.var['mt']` already exists. If mitochondrial flags are needed, derive them from gene_symbols, feature_name, or var_names.\n"
            "- For single-cell integration, fewer than 2 valid samples means the preconditions are not met; do not claim batch integration succeeded or emit placeholder success artifacts.\n"
            "- For web_search: cite verifiable sources. When stating time-sensitive or factual claims, include URLs from the tool JSON "
            "`results` list (title/url) in your final answer. If `results` is empty and the tool response has no URLs, say sources were "
            "not returned and avoid presenting specific claims as independently verified.\n"
        )

    def _build_protocol_boundary_block(self, mode: str) -> str:
        if mode == "native":
            return (
                "=== PROTOCOL BOUNDARY (NATIVE TOOL CALLING) ===\n"
                "- Use native tool calls for actions and submit_final_answer to finish.\n"
                "- Do NOT output legacy JSON keys like thinking/action/final_answer in plain text.\n"
                "- Do NOT output structured-agent JSON keys like llm_reply/actions.\n"
            )
        return (
            "=== PROTOCOL BOUNDARY (LEGACY JSON) ===\n"
            "- Respond with valid JSON only using keys: thinking, action, final_answer.\n"
            "- Do NOT output structured-agent JSON keys like llm_reply/actions.\n"
            "- action is null or an object: {\"tool\": \"name\", \"params\": {...}}.\n"
            "- final_answer is null until you are ready to conclude.\n"
        )

    @staticmethod
    def _is_brief_execute_followup_context(context: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(context, dict):
            return False
        tier = str(context.get("request_tier") or "").strip().lower()
        intent_type = str(context.get("intent_type") or "").strip().lower()
        brevity_hint = bool(context.get("brevity_hint"))
        if tier != "execute" or not brevity_hint:
            return False
        return intent_type in {
            "execute_task",
            "local_mutation",
            "local_read",
            "local_inspect",
        }

    @staticmethod
    def _append_recent_chat_history(prompt: str, context: Optional[Dict[str, Any]]) -> str:
        if not context:
            return prompt
        history = context.get("chat_history", [])
        if not history:
            return prompt
        brief_execute_followup = DeepThinkAgent._is_brief_execute_followup_context(context)
        raw_lim = context.get("chat_history_max_messages")
        if isinstance(raw_lim, int) and raw_lim > 0:
            limit = min(raw_lim, CHAT_HISTORY_ABS_MAX)
        else:
            try:
                limit = max(
                    1,
                    min(
                        CHAT_HISTORY_ABS_MAX,
                        int(getattr(get_settings(), "chat_history_max_messages", 80)),
                    ),
                )
            except Exception:
                limit = 80
        if brief_execute_followup:
            limit = min(limit, 6)
            filtered_history = []
            for msg in history:
                content = str(msg.get("content") or "").strip()
                if not content:
                    continue
                filtered_history.append(msg)
            history = filtered_history
        recent = history[-limit:] if len(history) > limit else history
        lines = []
        for msg in recent:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            clip_limit = 240 if brief_execute_followup else 500
            if len(content) > clip_limit:
                content = content[:clip_limit] + "..."
            lines.append(f"[{role}]: {content}")
        header = "=== RECENT CONTINUATION CONTEXT ===" if brief_execute_followup else "=== RECENT CONVERSATION ==="
        return prompt + f"\n{header}\n" + "\n".join(lines)

    @staticmethod
    def _clip_reference_text(value: Any, *, limit: int = 800) -> str:
        text = " ".join(str(value or "").split()).strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)] + "..."

    @classmethod
    def _append_reference_context(
        cls,
        prompt: str,
        context: Optional[Dict[str, Any]],
    ) -> str:
        if not context:
            return prompt

        blocks: List[str] = []
        brief_execute_followup = cls._is_brief_execute_followup_context(context)

        user_message = context.get("user_message")
        if isinstance(user_message, str) and user_message.strip():
            blocks.append("=== ORIGINAL USER REQUEST ===")
            blocks.append(cls._clip_reference_text(user_message, limit=1200))

        if brief_execute_followup:
            blocks.append("=== RESPONSE FOCUS ===")
            blocks.append("- Focus on the current execution result or current task outcome.")
            blocks.append("- Do not recap prior project milestones, older runs, or historical progress tables.")
            blocks.append("- Do not append next-step suggestions unless the user explicitly asks for them.")
            blocks.append("- If a continuation summary already names the target file, path, task, or prior blocker, continue from that anchor instead of restarting workspace discovery.")

        continuation_summary = context.get("continuation_summary")
        if brief_execute_followup and isinstance(continuation_summary, dict):
            continuation_lines: List[str] = []
            previous_user_request = cls._clip_reference_text(
                continuation_summary.get("previous_user_request"),
                limit=240,
            )
            if previous_user_request:
                continuation_lines.append(f"- Previous user request: {previous_user_request}")
            previous_assistant_summary = cls._clip_reference_text(
                continuation_summary.get("previous_assistant_summary"),
                limit=280,
            )
            if previous_assistant_summary:
                continuation_lines.append(f"- Previous assistant summary: {previous_assistant_summary}")
            active_subject = cls._clip_reference_text(
                continuation_summary.get("active_subject"),
                limit=240,
            )
            if active_subject:
                continuation_lines.append(f"- Active subject: {active_subject}")
            known_paths = continuation_summary.get("known_paths")
            if isinstance(known_paths, list):
                for path in known_paths[:4]:
                    path_text = cls._clip_reference_text(path, limit=240)
                    if path_text:
                        continuation_lines.append(f"- Known path anchor: {path_text}")
            known_filenames = continuation_summary.get("known_filenames")
            if isinstance(known_filenames, list):
                filename_texts = [
                    cls._clip_reference_text(name, limit=120)
                    for name in known_filenames[:4]
                    if cls._clip_reference_text(name, limit=120)
                ]
                if filename_texts:
                    continuation_lines.append(
                        f"- Known filename anchors: {', '.join(filename_texts)}"
                    )
            latest_tool_result = cls._clip_reference_text(
                continuation_summary.get("latest_tool_result"),
                limit=260,
            )
            if latest_tool_result:
                continuation_lines.append(f"- Latest tool result: {latest_tool_result}")
            recent_image_artifacts = continuation_summary.get("recent_image_artifacts")
            if isinstance(recent_image_artifacts, list):
                image_texts = [
                    cls._clip_reference_text(item, limit=160)
                    for item in recent_image_artifacts[:4]
                    if cls._clip_reference_text(item, limit=160)
                ]
                if image_texts:
                    continuation_lines.append(
                        f"- Recent image artifacts: {', '.join(image_texts)}"
                    )
            last_failure = cls._clip_reference_text(
                continuation_summary.get("last_failure"),
                limit=240,
            )
            if last_failure:
                continuation_lines.append(f"- Last known blocker: {last_failure}")
            if continuation_lines:
                blocks.append("=== EXECUTION CONTINUATION SUMMARY ===")
                blocks.extend(continuation_lines)

        tool_results = context.get("recent_tool_results", [])
        if isinstance(tool_results, list) and tool_results:
            blocks.append("=== RECENT TOOL RESULTS ===")
            recent_items = tool_results[-1:] if brief_execute_followup else tool_results[-3:]
            for item in recent_items:
                if not isinstance(item, dict):
                    continue
                tool_name = str(item.get("tool") or item.get("name") or "unknown").strip()
                summary = cls._clip_reference_text(item.get("summary"), limit=240)
                if summary:
                    blocks.append(f"- {tool_name}: {summary}")
                else:
                    blocks.append(f"- {tool_name}")

        paper_context_paths = context.get("paper_context_paths", [])
        if isinstance(paper_context_paths, list):
            normalized_paths = [
                str(path).strip()
                for path in paper_context_paths
                if str(path).strip()
            ]
            if normalized_paths:
                blocks.append("=== PAPER CONTEXT PATHS ===")
                for path in normalized_paths[:10]:
                    blocks.append(f"- {path}")

        if blocks:
            prompt = prompt + "\n" + "\n".join(blocks)
        return cls._append_recent_chat_history(prompt, context)

    async def think(
        self,
        user_query: str,
        context: Optional[Dict[str, Any]] = None,
        task_context: Optional[TaskExecutionContext] = None,
    ) -> DeepThinkResult:
        """
        Executes the deep thinking loop with streaming output.

        Automatically uses native tool calling when the LLM client supports it,
        falling back to prompt-based JSON parsing otherwise.
        """
        if not user_query or not user_query.strip():
            raise ValueError("User query cannot be empty")
        if len(user_query) > 10000:
            raise ValueError("User query too long (max 10000 chars)")

        if self._supports_native_tools():
            logger.info("[DEEP_THINK] Using native tool calling path")
            return await self._think_native(user_query.strip(), context, task_context)

        return await self._think_prompt_based(user_query.strip(), context, task_context)

    # ------------------------------------------------------------------ #
    #  Native tool calling path                                           #
    # ------------------------------------------------------------------ #

    async def _think_native(
        self,
        user_query: str,
        context: Optional[Dict[str, Any]] = None,
        task_context: Optional[TaskExecutionContext] = None,
    ) -> DeepThinkResult:
        from app.services.execution.async_tool_executor import (
            PendingToolCall,
            classify_tool_concurrency,
            execute_with_concurrency,
        )

        from app.services.context.context_manager import (
            ContextWindowManager,
            build_summarization_prompt,
        )

        context = dict(context or {})
        thinking_steps: List[ThinkingStep] = []
        tools_used: List[str] = []
        tool_schemas = build_tool_schemas(self.available_tools)

        system_prompt = self._build_native_system_prompt(context, task_context)
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query},
        ]

        llm_model = getattr(self.llm_client, "model", "") or ""
        ctx_mgr = ContextWindowManager(model=llm_model)

        async def _summarize_for_compaction(text: str) -> str:
            prompt = build_summarization_prompt(text)
            result = await self.llm_client.chat_async(prompt)
            return str(result or "").strip()

        iteration = 0
        final_answer = ""
        fallback_used = False
        confidence = 0.0
        last_tool_cycle_signature: Optional[str] = None
        identical_tool_cycle_count = 0
        probe_only_execution_cycles = 0
        forced_probe_followthrough_attempts = 0
        forced_handoff_followthrough_attempts = 0
        had_real_execution_tool = False
        partial_completion_retry_count = 0
        _MAX_PARTIAL_RETRIES = 6
        _MAX_HANDOFF_ITERATION_EXTENSIONS = 4
        last_real_execution_tool_results: List[Dict[str, Any]] = []
        force_verified_execution_finalization = False
        pending_handoff_task_id: Optional[int] = None
        pending_handoff_previous_task_id: Optional[int] = None
        structured_plan_finalize_nudge_plan_id: Optional[int] = None
        runtime_iteration_limit = self.max_iterations
        handoff_iteration_extensions = 0

        logger.info("[DEEP_THINK_NATIVE] Starting for: %s", user_query[:50])

        while iteration < runtime_iteration_limit:
            await self._pause_event.wait()
            if self.cancel_event and self.cancel_event.is_set():
                logger.info("[DEEP_THINK_NATIVE] Cancelled by user")
                break

            if self.steer_drain:
                steers = self.steer_drain()
                for steer_text in steers:
                    messages.append({
                        "role": "user",
                        "content": f"[User mid-run guidance]: {steer_text}",
                    })
                    logger.info(
                        "[DEEP_THINK_NATIVE] Injected user steer at iteration %d: %s",
                        iteration + 1,
                        steer_text[:120],
                    )
                    if self.on_steer_ack:
                        await self._safe_generic_callback(
                            self.on_steer_ack, steer_text, iteration + 1
                        )

            messages = await ctx_mgr.compact_if_needed(
                messages, summarizer=_summarize_for_compaction,
            )

            iteration += 1
            current_step = ThinkingStep(
                iteration=iteration,
                thought="",
                action=None,
                action_result=None,
                self_correction=None,
                timestamp=datetime.now(),
                status="thinking",
            )
            if self.on_thinking:
                await self._safe_callback(current_step)

            try:
                async def _on_delta(chunk: str) -> None:
                    if self.on_thinking_delta:
                        await self._safe_delta_callback(iteration, chunk)

                async def _on_reasoning_delta(chunk: str) -> None:
                    if self.on_reasoning_delta:
                        try:
                            ret = self.on_reasoning_delta(iteration, chunk)
                            if asyncio.iscoroutine(ret):
                                await ret
                        except Exception:
                            pass

                result = await self.llm_client.stream_chat_with_tools_async(
                    messages=messages,
                    tools=tool_schemas,
                    tool_choice="auto",
                    on_content_delta=_on_delta,
                    on_reasoning_delta=_on_reasoning_delta,
                    enable_thinking=self.enable_thinking,
                    thinking_budget=self.thinking_budget,
                )
            except Exception as exc:
                error_detail = _describe_exception(exc)
                logger.exception(
                    "[DEEP_THINK_NATIVE] LLM call failed at iteration %d: %s",
                    iteration,
                    error_detail,
                )
                current_step.status = "error"
                current_step.thought = f"Error: {error_detail}"
                current_step.finished_at = datetime.now()
                thinking_steps.append(current_step)
                if self.on_thinking:
                    await self._safe_callback(current_step)
                continue

            current_step.thought = result.content or ""

            if result.tool_calls:
                tool_calls = list(result.tool_calls)
                final_call = next((tc for tc in tool_calls if tc.name == "submit_final_answer"), None)
                executable_calls = [tc for tc in tool_calls if tc.name != "submit_final_answer"]
                replacement_task_id = self._verification_only_cycle_replacement_task_id(
                    executable_calls,
                    task_context=task_context,
                    had_real_execution_tool=had_real_execution_tool,
                )
                if replacement_task_id is not None:
                    template_call = executable_calls[0]
                    executable_calls = [
                        type(template_call)(
                            id=str(getattr(template_call, "id", "") or f"native_{iteration}_rerun_task"),
                            name="rerun_task",
                            arguments={"task_id": replacement_task_id},
                        )
                    ]
                    logger.info(
                        "[DEEP_THINK_NATIVE] Replaced verify_task-only cycle with rerun_task for task_id=%s at iteration=%s",
                        replacement_task_id,
                        iteration,
                    )
                    current_step.self_correction = (
                        f"Rejected verification-only follow-up for bound Task {replacement_task_id} and replaced it with rerun_task."
                    )
                if force_verified_execution_finalization and executable_calls:
                    allowed_summary_calls = [
                        tc
                        for tc in executable_calls
                        if str(getattr(tc, "name", "") or "").strip().lower() == "result_interpreter"
                    ]
                    skipped_names = [
                        str(getattr(tc, "name", "") or "").strip() or "<unknown>"
                        for tc in executable_calls
                        if tc not in allowed_summary_calls
                    ]
                    if skipped_names:
                        logger.info(
                            "[DEEP_THINK_NATIVE] Skipping post-success exploratory tool calls: %s",
                            ",".join(skipped_names),
                        )
                        current_step.self_correction = (
                            "Skipped non-summary tool calls after verified task completion and forced finalization mode."
                        )
                        executable_calls = allowed_summary_calls
                        if not executable_calls and final_call is None:
                            current_step.status = "analyzing"
                            current_step.finished_at = datetime.now()
                            thinking_steps.append(current_step)
                            if self.on_thinking:
                                await self._safe_callback(current_step)
                            messages.append({"role": "assistant", "content": result.content or ""})
                            messages.append(
                                {
                                    "role": "user",
                                    "content": self._build_verified_execution_finalize_nudge(
                                        task_context=task_context,
                                        user_query=user_query,
                                    ),
                                }
                            )
                            continue

                if executable_calls:
                    bound_task_before_cycle = self._current_bound_task_id(task_context)
                    action_payload = {
                        "tools": [
                            {
                                "tool": tc.name,
                                "params": tc.arguments,
                                "tool_call_id": tc.id or f"native_{iteration}_{idx}",
                            }
                            for idx, tc in enumerate(executable_calls)
                        ]
                    }
                    current_step.action = json.dumps(action_payload, ensure_ascii=False)
                    current_step.status = "calling_tool"
                    thinking_steps.append(current_step)
                    if self.on_thinking:
                        await self._safe_callback(current_step)

                    pending = []
                    for idx, tc in enumerate(executable_calls):
                        name = str(getattr(tc, "name", "") or "")
                        pending.append(PendingToolCall(
                            index=idx,
                            tool_name=name,
                            coroutine_factory=lambda _tc=tc, _idx=idx: self._execute_native_tool_call(
                                tc=_tc, iteration=iteration, index=_idx,
                            ),
                            is_concurrent_safe=classify_tool_concurrency(name),
                        ))
                    tool_results = await execute_with_concurrency(pending)

                    for item in tool_results:
                        tool_name = str(item.get("tool_name") or "")
                        if tool_name and tool_name not in tools_used:
                            tools_used.append(tool_name)

                    self._append_tool_cycle_messages(
                        messages=messages,
                        tool_results=tool_results,
                        assistant_content=result.content or "",
                        current_step=current_step,
                    )

                    if final_call is None:
                        created_plan = self._extract_successful_created_plan_from_tool_results(
                            tool_results
                        )
                        created_plan_id = (
                            int(created_plan["plan_id"])
                            if isinstance(created_plan, dict)
                            and created_plan.get("plan_id") is not None
                            else None
                        )
                        if (
                            created_plan_id is not None
                            and self._requires_structured_plan()
                            and self._plan_request_mode() in {"create", "create_new"}
                            and structured_plan_finalize_nudge_plan_id != created_plan_id
                        ):
                            messages.append(
                                {
                                    "role": "user",
                                    "content": self._build_created_plan_finalize_nudge(
                                        user_query=user_query,
                                        plan_id=created_plan_id,
                                        plan_title=(
                                            created_plan.get("plan_title")
                                            if isinstance(created_plan, dict)
                                            else None
                                        ),
                                    ),
                                }
                            )
                            structured_plan_finalize_nudge_plan_id = created_plan_id
                            logger.info(
                                "[DEEP_THINK_NATIVE] Injected finalize nudge after successful plan creation: plan_id=%s",
                                created_plan_id,
                            )

                    tool_cycle_signature = self._build_tool_cycle_signature(tool_results)
                    if tool_cycle_signature and tool_cycle_signature == last_tool_cycle_signature:
                        identical_tool_cycle_count += 1
                    else:
                        last_tool_cycle_signature = tool_cycle_signature
                        identical_tool_cycle_count = 0

                    if identical_tool_cycle_count >= self.MAX_IDENTICAL_TOOL_CALL_CYCLES:
                        repeated_cycles = identical_tool_cycle_count + 1
                        current_step.status = "done"
                        current_step.self_correction = (
                            "Stopped repeated identical tool polling to avoid an unproductive loop."
                        )
                        if self.on_thinking:
                            await self._safe_callback(current_step)
                        final_answer = self._build_repetition_stop_answer(
                            tool_results=tool_results,
                            repeated_cycles=repeated_cycles,
                        )
                        confidence = max(
                            confidence,
                            0.75 if self._contains_tool(tool_results, "phagescope") else 0.5,
                        )
                        logger.warning(
                            "[DEEP_THINK_NATIVE] Stopped repeated tool loop at iteration=%s repeated_cycles=%s tools=%s",
                            iteration,
                            repeated_cycles,
                            ",".join(
                                sorted(
                                    {
                                        str(item.get("tool_name") or "")
                                        for item in tool_results
                                        if item.get("tool_name")
                                    }
                                )
                            ),
                        )
                        if self.on_final_delta and final_answer:
                            await self._stream_final_answer(final_answer)
                        break

                    is_probe_only_cycle = self._is_probe_only_execution_cycle(
                        tool_results,
                        task_context=task_context,
                    )
                    if is_probe_only_cycle:
                        probe_only_execution_cycles += 1
                        probe_limit = 2 if had_real_execution_tool else 3
                        if (
                            not had_real_execution_tool
                            and probe_only_execution_cycles >= 2
                            and forced_probe_followthrough_attempts < 1
                            and self._can_force_probe_followthrough_execution(task_context)
                        ):
                            forced_probe_followthrough_attempts += 1
                            forced_result = await self._execute_forced_probe_followthrough(
                                task_context=task_context,
                                user_query=user_query,
                                iteration=iteration,
                                probe_only_execution_cycles=probe_only_execution_cycles,
                            )
                            forced_tool_name = str(forced_result.get("tool_name") or "")
                            if forced_tool_name and forced_tool_name not in tools_used:
                                tools_used.append(forced_tool_name)
                            self._append_tool_cycle_messages(
                                messages=messages,
                                tool_results=[forced_result],
                                assistant_content="",
                                current_step=current_step,
                            )
                            tool_results = [forced_result]
                            last_tool_cycle_signature = self._build_tool_cycle_signature(tool_results)
                            identical_tool_cycle_count = 0
                            is_probe_only_cycle = self._is_probe_only_execution_cycle(
                                tool_results,
                                task_context=task_context,
                            )
                            if not is_probe_only_cycle:
                                probe_only_execution_cycles = 0
                                current_step.self_correction = (
                                    "Detected repeated observation-only probing despite available upstream artifacts; "
                                    "forced code_executor execution."
                                )
                    if is_probe_only_cycle:
                        if probe_only_execution_cycles >= probe_limit:
                            # Hard stop for infinite observation loops — always active regardless of
                            # had_real_execution_tool. Without this, a post-execution AI that keeps
                            # reading non-existent files would silently burn through max_iterations.
                            current_step.status = "done"
                            if had_real_execution_tool:
                                # Task was executed; post-execution probing exceeded limit.
                                # Do NOT return BLOCKED_DEPENDENCY — we know the task ran.
                                # The AI never submitted submit_final_answer, so final_answer
                                # is likely empty here.  Return a neutral completion notice.
                                current_step.self_correction = (
                                    "Stopped repeated post-execution observation-only probing."
                                )
                                if not final_answer:
                                    final_answer = self._build_post_execution_probe_stop_answer(
                                        task_context=task_context,
                                        user_query=user_query,
                                        steps=[*thinking_steps, current_step],
                                        tool_results=last_real_execution_tool_results,
                                    )
                            else:
                                if self._explicit_task_override_active(task_context):
                                    # For explicit task override, do NOT return BLOCKED_DEPENDENCY.
                                    # The user explicitly requested this task — give a neutral
                                    # status instead of demanding manual prerequisite work.
                                    current_step.self_correction = (
                                        "Stopped observation-only probing for explicit task override; "
                                        "returning execution status instead of blocked-dependency."
                                    )
                                    task_label = ""
                                    if task_context and task_context.task_id is not None:
                                        task_label = f"Task {task_context.task_id}"
                                        if task_context.task_name:
                                            task_label = f"{task_label} ({task_context.task_name})"
                                    final_answer = (
                                        f"{task_label or 'The bound task'} 的执行尝试未能产生预期输出。"
                                        "已尝试自动执行但未成功完成，请检查任务指令和上游数据是否就绪，然后重试。"
                                    )
                                else:
                                    current_step.self_correction = (
                                        "Stopped repeated observation-only probing and returned a blocked-dependency conclusion."
                                    )
                                    final_answer = self._build_blocked_dependency_answer(
                                        task_context=task_context,
                                        user_query=user_query,
                                        tool_results=tool_results,
                                    )
                            confidence = max(confidence, 0.8)
                            logger.warning(
                                "[DEEP_THINK_NATIVE] Stopped after %s consecutive probe-only execution cycles at iteration=%s had_real_execution_tool=%s",
                                probe_only_execution_cycles,
                                iteration,
                                had_real_execution_tool,
                            )
                            if self.on_thinking:
                                await self._safe_callback(current_step)
                            if self.on_final_delta and final_answer:
                                await self._stream_final_answer(final_answer)
                            break

                        if not had_real_execution_tool:
                            nudge = self._build_probe_only_followthrough_nudge(
                                task_context=task_context,
                                user_query=user_query,
                                stage=probe_only_execution_cycles,
                            )
                            messages.append({"role": "user", "content": nudge})
                            logger.info(
                                "[DEEP_THINK_NATIVE] Injected execute followthrough nudge after probe-only cycle=%s at iteration=%s",
                                probe_only_execution_cycles,
                                iteration,
                            )
                            current_step.self_correction = (
                                "Detected observation-only exploration during a bound execute_task request; injected a followthrough nudge."
                            )
                        else:
                            nudge = self._build_post_execution_summary_nudge(
                                task_context=task_context,
                                user_query=user_query,
                                stage=probe_only_execution_cycles,
                            )
                            messages.append({"role": "user", "content": nudge})
                            logger.info(
                                "[DEEP_THINK_NATIVE] Injected post-execution summary nudge after probe-only cycle=%s at iteration=%s",
                                probe_only_execution_cycles,
                                iteration,
                            )
                            current_step.self_correction = (
                                "Detected post-execution observation-only probing; injected a summary nudge."
                            )
                    else:
                        probe_only_execution_cycles = 0
                        # Only mark had_real_execution_tool when a code-running
                        # tool actually executed.  Coordination tools like
                        # plan_operation still reset the probe counter (they ARE
                        # a deliberate action, not passive observation) but must
                        # NOT set the flag — otherwise the hard-stop emits a
                        # misleading "task code executed" message when no code
                        # was ever run.
                        if any(self._tool_counts_as_real_execution(item) for item in tool_results):
                            had_real_execution_tool = True
                            last_real_execution_tool_results = [
                                item for item in tool_results if self._tool_counts_as_real_execution(item)
                            ]
                            executed_pending_handoff = (
                                pending_handoff_task_id is not None
                                and bound_task_before_cycle == pending_handoff_task_id
                            )
                            bound_task_after_cycle = self._current_bound_task_id(task_context)
                            if (
                                bound_task_before_cycle is not None
                                and bound_task_after_cycle is not None
                                and bound_task_after_cycle != bound_task_before_cycle
                            ):
                                pending_handoff_previous_task_id = bound_task_before_cycle
                                pending_handoff_task_id = bound_task_after_cycle
                                forced_handoff_followthrough_attempts = 0
                                had_real_execution_tool = False
                                last_real_execution_tool_results = []
                                probe_only_execution_cycles = 0
                                partial_completion_retry_count = 0
                                messages.append(
                                    {
                                        "role": "user",
                                        "content": self._build_task_handoff_execution_nudge(
                                            task_context=task_context,
                                            user_query=user_query,
                                            previous_task_id=bound_task_before_cycle,
                                            next_task_id=bound_task_after_cycle,
                                        ),
                                    }
                                )
                                if (
                                    handoff_iteration_extensions < _MAX_HANDOFF_ITERATION_EXTENSIONS
                                    and iteration >= runtime_iteration_limit - 1
                                ):
                                    runtime_iteration_limit += 1
                                    handoff_iteration_extensions += 1
                                    logger.info(
                                        "[DEEP_THINK_NATIVE] Extended iteration budget after task handoff previous=%s next=%s new_limit=%s extension=%s",
                                        bound_task_before_cycle,
                                        bound_task_after_cycle,
                                        runtime_iteration_limit,
                                        handoff_iteration_extensions,
                                    )
                                logger.info(
                                    "[DEEP_THINK_NATIVE] Detected task handoff from %s to %s at iteration=%s; injected execute-next-task nudge",
                                    bound_task_before_cycle,
                                    bound_task_after_cycle,
                                    iteration,
                                )
                                current_step.self_correction = (
                                    f"Detected task handoff from {bound_task_before_cycle} to "
                                    f"{bound_task_after_cycle}; injected an execute-next-task nudge."
                                )
                                # Skip partial-completion / finalization checks this
                                # iteration — the handoff target hasn't been executed
                                # yet and finalization would prematurely end the run.
                                current_step.status = "analyzing"
                                if self.on_thinking:
                                    await self._safe_callback(current_step)
                                continue
                            elif executed_pending_handoff:
                                pending_handoff_task_id = None
                                pending_handoff_previous_task_id = None
                                forced_handoff_followthrough_attempts = 0

                        # --- Partial completion retry ---
                        partial_info = self._detect_partial_completion_in_tool_results(tool_results)
                        if (
                            partial_info
                            and partial_completion_retry_count < _MAX_PARTIAL_RETRIES
                            and self._current_bound_task_id(task_context) == bound_task_before_cycle
                        ):
                            partial_completion_retry_count += 1
                            nudge = self._build_partial_completion_retry_nudge(
                                partial_info,
                                task_context=task_context,
                                user_query=user_query,
                                retry_count=partial_completion_retry_count,
                            )
                            messages.append({"role": "user", "content": nudge})
                            logger.info(
                                "[DEEP_THINK_NATIVE] Partial completion retry nudge: ratio=%s retry=%d iter=%d",
                                partial_info.get("partial_ratio"),
                                partial_completion_retry_count,
                                iteration,
                            )
                            current_step.self_correction = (
                                f"Detected partial completion ({partial_info.get('partial_ratio', '?/?')}); "
                                f"injected retry nudge #{partial_completion_retry_count}."
                            )
                        elif self._should_force_verified_execution_finalization(
                            task_context=task_context,
                            tool_results=tool_results,
                            had_real_execution_tool=had_real_execution_tool,
                        ):
                            force_verified_execution_finalization = True
                            messages.append(
                                {
                                    "role": "user",
                                    "content": self._build_verified_execution_finalize_nudge(
                                        task_context=task_context,
                                        user_query=user_query,
                                    ),
                                }
                            )
                            logger.info(
                                "[DEEP_THINK_NATIVE] Entered verified-execution finalization mode at iteration=%s",
                                iteration,
                            )
                            current_step.self_correction = (
                                "Detected verified task completion with no remaining pending tasks; "
                                "injected a finalization-only nudge."
                            )

                    current_step.status = "analyzing"
                    if self.on_thinking:
                        await self._safe_callback(current_step)
                    continue

                if final_call:
                    candidate_answer = str(final_call.arguments.get("answer", "") or "")
                    raw_conf = final_call.arguments.get("confidence", 0.8)
                    try:
                        confidence = max(0.0, min(1.0, float(raw_conf)))
                    except (TypeError, ValueError):
                        confidence = 0.8
                    current_step.status = "done"
                    current_step.finished_at = datetime.now()
                    if (
                        probe_only_execution_cycles >= 2
                        and not had_real_execution_tool
                        and self._is_execute_task_request()
                        and self._has_bound_task_context(task_context)
                        and not self._looks_like_blocked_dependency_answer(candidate_answer)
                        # Do not replace with BLOCKED_DEPENDENCY when the user
                        # explicitly requested this task — forced execution
                        # should have run or the LLM's natural answer is
                        # preferable to a generic "please provide prerequisites"
                        # message that the user has already complained about.
                        and not self._explicit_task_override_active(task_context)
                    ):
                        current_step.self_correction = (
                            "Rejected a conclusion after repeated observation-only probing and replaced it with a blocked-dependency answer."
                        )
                        final_answer = self._build_blocked_dependency_answer(
                            task_context=task_context,
                            user_query=user_query,
                            tool_results=[],
                        )
                        if self.on_final_delta and final_answer:
                            await self._stream_final_answer(final_answer)
                        thinking_steps.append(current_step)
                        if self.on_thinking:
                            await self._safe_callback(current_step)
                        break
                    if not self._is_valid_final_answer(candidate_answer, user_query=user_query):
                        current_step.self_correction = (
                            "Discarded a process-only conclusion and switching to fallback synthesis."
                        )
                        final_answer = ""
                    else:
                        structured_plan_outcome = self._summarize_structured_plan_outcome(
                            thinking_steps,
                            user_query=user_query,
                        )
                        if structured_plan_outcome.get("required") and not structured_plan_outcome.get("satisfied"):
                            current_step.self_correction = (
                                "Rejected the final answer because the required structured plan was not created or updated yet."
                            )
                            final_answer = ""
                            thinking_steps.append(current_step)
                            if self.on_thinking:
                                await self._safe_callback(current_step)
                            messages.append({"role": "assistant", "content": result.content or ""})
                            messages.append(
                                {
                                    "role": "user",
                                    "content": self._get_structured_plan_retry_prompt(),
                                }
                            )
                            continue
                        final_answer = candidate_answer
                    thinking_steps.append(current_step)
                    if self.on_thinking:
                        await self._safe_callback(current_step)
                    if self.on_final_delta and final_answer:
                        await self._stream_final_answer(final_answer)
                    break
            else:
                # No tool calls – pure thinking text.
                # Try to parse structured JSON actions from content as compatibility fallback.
                parsed_actions = self._try_parse_structured_actions(result.content or "")
                if parsed_actions:
                    logger.info(
                        "[DEEP_THINK_NATIVE] Parsed %d structured actions from text fallback",
                        len(parsed_actions),
                    )
                    for pa in parsed_actions:
                        pa_name = pa.get("name", "")
                        pa_params = pa.get("parameters") or {}
                        if pa_name and pa_name in self.available_tools:
                            if pa_name not in tools_used:
                                tools_used.append(pa_name)
                            current_step.action = json.dumps(
                                {"tool": pa_name, "params": pa_params}, ensure_ascii=False
                            )
                            current_step.status = "calling_tool"
                            if self.on_thinking:
                                await self._safe_callback(current_step)
                            try:
                                from app.services.execution.tool_executor import UnifiedToolExecutor
                                timeout = UnifiedToolExecutor.TOOL_TIMEOUTS.get(pa_name, self.tool_timeout)
                                tool_result = await asyncio.wait_for(
                                    self.tool_executor(pa_name, pa_params),
                                    timeout=timeout,
                                )
                                try:
                                    action_result_text = json.dumps(tool_result, ensure_ascii=False, default=str)
                                except Exception:
                                    action_result_text = str(tool_result)
                                current_step.action_result = action_result_text
                                messages.append({"role": "assistant", "content": result.content or ""})
                                messages.append({"role": "user", "content": f"Tool Output: {action_result_text}"})
                            except Exception as exc:
                                current_step.action_result = f"Error: {exc}"
                                messages.append({"role": "assistant", "content": result.content or ""})
                                messages.append({"role": "user", "content": f"Tool Error: {exc}"})
                    current_step.status = "analyzing"
                    current_step.finished_at = datetime.now()
                    thinking_steps.append(current_step)
                    if self.on_thinking:
                        await self._safe_callback(current_step)
                else:
                    if (
                        pending_handoff_task_id is not None
                        and pending_handoff_previous_task_id is not None
                        and forced_handoff_followthrough_attempts < 1
                        and self._can_force_handoff_followthrough_execution(
                            task_context,
                            next_task_id=pending_handoff_task_id,
                        )
                    ):
                        forced_handoff_followthrough_attempts += 1
                        prior_handoff_task_id = pending_handoff_task_id
                        forced_result = await self._execute_forced_handoff_followthrough(
                            task_context=task_context,
                            user_query=user_query,
                            iteration=iteration,
                            previous_task_id=pending_handoff_previous_task_id,
                            next_task_id=prior_handoff_task_id,
                            reason="no_tool_after_handoff",
                        )
                        forced_tool_name = str(forced_result.get("tool_name") or "")
                        if forced_tool_name and forced_tool_name not in tools_used:
                            tools_used.append(forced_tool_name)
                        current_step.action = json.dumps(
                            {
                                "tools": [
                                    {
                                        "tool": "code_executor",
                                        "params": {"task": "[forced handoff followthrough]"},
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        )
                        current_step.action_result = forced_result.get("tool_result_text")
                        self._append_tool_cycle_messages(
                            messages=messages,
                            tool_results=[forced_result],
                            assistant_content=result.content or "",
                            current_step=current_step,
                        )
                        last_tool_cycle_signature = self._build_tool_cycle_signature([forced_result])
                        identical_tool_cycle_count = 0
                        probe_only_execution_cycles = 0
                        bound_task_after_forced = self._current_bound_task_id(task_context)
                        if self._tool_counts_as_real_execution(forced_result):
                            had_real_execution_tool = True
                            last_real_execution_tool_results = [forced_result]
                        if (
                            bound_task_after_forced is not None
                            and bound_task_after_forced != prior_handoff_task_id
                        ):
                            pending_handoff_previous_task_id = prior_handoff_task_id
                            pending_handoff_task_id = bound_task_after_forced
                            forced_handoff_followthrough_attempts = 0
                            had_real_execution_tool = False
                            last_real_execution_tool_results = []
                            partial_completion_retry_count = 0
                            messages.append(
                                {
                                    "role": "user",
                                    "content": self._build_task_handoff_execution_nudge(
                                        task_context=task_context,
                                        user_query=user_query,
                                        previous_task_id=prior_handoff_task_id,
                                        next_task_id=bound_task_after_forced,
                                    ),
                                }
                            )
                            if (
                                handoff_iteration_extensions < _MAX_HANDOFF_ITERATION_EXTENSIONS
                                and iteration >= runtime_iteration_limit - 1
                            ):
                                runtime_iteration_limit += 1
                                handoff_iteration_extensions += 1
                                logger.info(
                                    "[DEEP_THINK_NATIVE] Extended iteration budget after forced handoff followthrough previous=%s next=%s new_limit=%s extension=%s",
                                    prior_handoff_task_id,
                                    bound_task_after_forced,
                                    runtime_iteration_limit,
                                    handoff_iteration_extensions,
                                )
                            current_step.self_correction = (
                                f"Detected a no-tool response after handoff to Task {prior_handoff_task_id}; "
                                f"forced code_executor execution and advanced again to Task {bound_task_after_forced}."
                            )
                        else:
                            pending_handoff_task_id = None
                            pending_handoff_previous_task_id = None
                            current_step.self_correction = (
                                f"Detected a no-tool response immediately after handoff to Task {prior_handoff_task_id}; "
                                "forced code_executor execution instead of allowing generic fallback."
                            )
                            if self._should_force_verified_execution_finalization(
                                task_context=task_context,
                                tool_results=[forced_result],
                                had_real_execution_tool=had_real_execution_tool,
                            ):
                                force_verified_execution_finalization = True
                                messages.append(
                                    {
                                        "role": "user",
                                        "content": self._build_verified_execution_finalize_nudge(
                                            task_context=task_context,
                                            user_query=user_query,
                                        ),
                                    }
                                )
                                logger.info(
                                    "[DEEP_THINK_NATIVE] Entered verified-execution finalization mode after forced handoff followthrough at iteration=%s",
                                    iteration,
                                )
                        current_step.status = "analyzing"
                        current_step.finished_at = datetime.now()
                        thinking_steps.append(current_step)
                        if self.on_thinking:
                            await self._safe_callback(current_step)
                        continue

                    # --- Early stop for light / standard tiers ---
                    # When the LLM produces a substantive text answer without
                    # any tool calls on a low-effort request, treat the content
                    # as the final answer immediately instead of forcing
                    # additional (empty) iterations + synthesis.
                    _tier_for_early_stop = self._request_tier()
                    _content_for_early_stop = (result.content or "").strip()
                    if (
                        _tier_for_early_stop in {"light", "standard"}
                        and _content_for_early_stop
                        and len(_content_for_early_stop) >= 20
                        and not self._is_execute_task_request()
                        and not self._requires_structured_plan()
                    ):
                        final_answer = _content_for_early_stop
                        confidence = max(confidence, 0.85)
                        current_step.status = "done"
                        current_step.finished_at = datetime.now()
                        thinking_steps.append(current_step)
                        if self.on_thinking:
                            await self._safe_callback(current_step)
                        logger.info(
                            "[DEEP_THINK_NATIVE] Early stop: tier=%s iteration=%s content_len=%d — "
                            "treating direct text as final answer",
                            _tier_for_early_stop,
                            iteration,
                            len(_content_for_early_stop),
                        )
                        if self.on_final_delta:
                            await self._stream_final_answer(final_answer)
                        break

                    current_step.finished_at = datetime.now()
                    thinking_steps.append(current_step)
                    if self.on_thinking:
                        await self._safe_callback(current_step)
                    messages.append({"role": "assistant", "content": result.content or ""})
                    messages.append({"role": "user", "content": self._get_next_step_prompt(iteration)})

            if self._skip_current_step:
                self._skip_current_step = False
                messages.append({"role": "user", "content": "Skip current branch and continue with the next reasoning step."})

            # Inject a strong nudge when nearing the iteration limit
            if not final_answer and iteration >= runtime_iteration_limit - 1:
                final_step_prompt = (
                    self._get_structured_plan_retry_prompt()
                    if self._requires_structured_plan()
                    else (
                        "IMPORTANT: You are about to reach the maximum number of thinking steps. "
                        "You MUST call submit_final_answer on the NEXT step with the best answer you can provide "
                        "based on all evidence gathered so far. Do NOT continue researching — synthesize NOW."
                    )
                )
                messages.append({
                    "role": "user",
                    "content": final_step_prompt,
                })

        if final_answer and not self._is_valid_final_answer(final_answer, user_query=user_query):
            logger.info("[DEEP_THINK_NATIVE] Rejected process-only final answer; switching to fallback synthesis")
            final_answer = ""

        # ---- Forced synthesis: one more LLM call with all evidence before falling back ----
        # Guard: only emit the "整集阻塞" blocker when the router has explicitly confirmed
        # (via explicit_scope_all_blocked in context) that every task in the named set is
        # unreachable.  Using explicit_task_override alone is insufficient — tools may have
        # run successfully but the LLM simply didn't emit a final answer, in which case the
        # normal forced-synthesis path is still appropriate.
        _explicit_override = task_context is not None and bool(
            getattr(task_context, "explicit_task_override", False)
        )
        _scope_all_blocked = bool((context or {}).get("explicit_scope_all_blocked"))
        if not final_answer and _explicit_override and _scope_all_blocked:
            _blocked_ids = list(getattr(task_context, "explicit_task_ids", None) or [])
            _id_str = ", ".join(str(t) for t in _blocked_ids) if _blocked_ids else "the requested tasks"
            final_answer = (
                f"Tasks [{_id_str}] could not be executed in this turn: "
                f"all tasks in the explicit set are blocked by unmet out-of-scope dependencies. "
                f"Please check the dependency status of the listed tasks and retry "
                f"after resolving any upstream blockers."
            )
            fallback_used = True
            logger.info(
                "[DEEP_THINK_NATIVE] explicit_scope_all_blocked: skipping forced synthesis, "
                "emitting structured blocker for task_ids=%s",
                _blocked_ids,
            )
            if self.on_final_delta:
                await self._stream_final_answer(final_answer)
        elif not final_answer and thinking_steps:
            logger.info("[DEEP_THINK_NATIVE] No final answer after %d iterations; attempting forced synthesis", iteration)
            final_answer = await self._forced_synthesis_from_steps(
                thinking_steps,
                user_query,
                messages,
                task_context=task_context,
            )
            if final_answer and self._should_reject_missing_task_definition_answer(
                final_answer,
                task_context=task_context,
            ):
                logger.warning(
                    "[DEEP_THINK_NATIVE] Rejected forced synthesis that incorrectly asked for missing task definitions while a bound task context existed."
                )
                final_answer = ""
            if final_answer:
                fallback_used = True
                confidence = max(confidence, 0.5)
                if self.on_final_delta:
                    await self._stream_final_answer(final_answer)

        if not final_answer:
            fallback_used = True
            final_answer = await self._fallback_answer_from_steps(
                thinking_steps,
                user_query,
                task_context=task_context,
            )
            confidence = max(confidence, 0.3)
            if self.on_final_delta and final_answer:
                await self._stream_final_answer(final_answer)

        tool_failures = self._collect_tool_failures_from_steps(thinking_steps)
        search_verified = self._search_verified_from_steps(thinking_steps)
        final_answer = self._apply_external_search_notice(
            final_answer,
            user_query=user_query,
            tool_failures=tool_failures,
            search_verified=search_verified,
        )
        execute_truth_answer = self._apply_execute_failure_truth_barrier(
            final_answer,
            user_query=user_query,
            steps=thinking_steps,
        )
        if execute_truth_answer != final_answer:
            fallback_used = True
        final_answer = execute_truth_answer
        structured_plan_outcome = self._summarize_structured_plan_outcome(
            thinking_steps,
            user_query=user_query,
        )
        final_answer = self._ensure_structured_plan_notice(
            final_answer,
            outcome=structured_plan_outcome,
            user_query=user_query,
        )
        final_answer = sanitize_professional_response_text(final_answer)

        try:
            summary = await self._generate_summary(thinking_steps, user_query)
        except Exception:
            summary = _default_deepthink_summary(user_query)

        return DeepThinkResult(
            final_answer=final_answer,
            thinking_steps=thinking_steps,
            total_iterations=iteration,
            tools_used=tools_used,
            confidence=confidence,
            thinking_summary=summary,
            tool_failures=tool_failures,
            search_verified=search_verified,
            fallback_used=fallback_used,
            structured_plan_required=bool(structured_plan_outcome.get("required")),
            structured_plan_satisfied=bool(structured_plan_outcome.get("satisfied")),
            structured_plan_state=structured_plan_outcome.get("state"),
            structured_plan_message=structured_plan_outcome.get("message"),
            structured_plan_plan_id=structured_plan_outcome.get("plan_id"),
            structured_plan_title=structured_plan_outcome.get("plan_title"),
            structured_plan_operation=structured_plan_outcome.get("operation"),
        )

    def _build_native_system_prompt(
        self,
        context: Optional[Dict[str, Any]] = None,
        task_context: Optional[TaskExecutionContext] = None,
    ) -> str:
        """System prompt for native tool calling mode (no JSON formatting rules)."""
        task_preamble = ""
        if task_context and task_context.task_instruction:
            lines = [
                "You are a task execution engine in DeepThink mode.",
                "Focus on completing the specific task with verifiable outputs.",
                "",
                "=== TASK CONTEXT ===",
            ]
            if task_context.task_id is not None:
                lines.append(f"Task ID: {task_context.task_id}")
            if task_context.task_name:
                lines.append(f"Task Name: {task_context.task_name}")
            lines.append(f"Instruction: {task_context.task_instruction}")
            if task_context.constraints:
                lines.append("Constraints:")
                for c in task_context.constraints:
                    lines.append(f"- {c}")
            if task_context.plan_outline:
                lines.append(f"Plan Outline (truncated):\n{task_context.plan_outline}")
            if task_context.dependency_outputs:
                lines.append("Dependency Outputs:")
                for dep in task_context.dependency_outputs[:6]:
                    lines.append(f"- {json.dumps(dep, ensure_ascii=False)[:600]}")
            if task_context.context_summary:
                lines.append("Task Reference Summary:")
                lines.append(self._clip_reference_text(task_context.context_summary, limit=1500))
            if task_context.context_sections:
                lines.append("Task Reference Sections:")
                for section in task_context.context_sections[:6]:
                    if not isinstance(section, dict):
                        continue
                    title = self._clip_reference_text(section.get("title") or "Section", limit=120)
                    content = self._clip_reference_text(section.get("content"), limit=700)
                    lines.append(f"- {title}: {content}")
            if task_context.paper_context_paths:
                lines.append("Paper Context Paths:")
                for path in task_context.paper_context_paths[:10]:
                    lines.append(f"- {path}")
            if task_context.skill_context:
                lines.append("")
                lines.append("=== SKILL GUIDANCE ===")
                lines.append(task_context.skill_context)
            lines.append("")
            task_preamble = "\n".join(lines) + "\n"

        prompt = task_preamble + (
            "You are a Deep Thinking AI Assistant.\n"
            "Your goal is to choose the right depth for the user's request: be thorough when needed, but do not over-research simple questions.\n\n"
            + self._build_shared_strategy_block()
            + self._build_request_tier_block()
            + self._build_structured_plan_requirement_block()
            + self._build_capability_floor_block()
            + self._build_grounded_tooling_block()
            + "\n"
            + "\n=== WORKFLOW ===\n"
            "1. First decide whether the request needs tools at all.\n"
            "2. For simple conversational or high-level requests, reason briefly and answer directly.\n"
            "3. For evidence-heavy or time-sensitive requests, gather targeted evidence with the minimum necessary tool usage.\n"
            "3a. When the user asks about files, data, or remote job state, prefer at least one relevant tool call before a final answer.\n"
            "4. Call submit_final_answer once the user's request is adequately answered.\n"
            "5. Keep iterative reasoning visible to the user, but concise and relevant.\n\n"
            + "=== AVAILABLE TOOLS ===\n"
            + "\n".join(f"- {tool}" for tool in self.available_tools)
            + "\n\n"
            + self._build_protocol_boundary_block("native")
            + "\n=== RULES ===\n"
            "- Do NOT call submit_final_answer prematurely.\n"
            "- Do NOT launch broad web/literature research unless the user asks for sources, latest information, deep analysis, or the task is clearly evidence-sensitive.\n"
            "- For simple requests, prefer zero-tool or one-tool answers; for factual questions, prefer tool verification over guessing.\n"
            "- Keep quick checks synchronous; use background workflows only for clearly long-running operations.\n"
            "- Prioritize directness, relevance, and user intent over maximum comprehensiveness.\n"
            "- Prioritize evidence-backed conclusions over speculation when evidence is actually needed.\n"
            "- Grounding (configs and files): Only report file paths, env vars, API provider names, or model IDs that appear "
            "verbatim in tool outputs from this session. If you intended to read path A but the tool output shows path B was read, "
            "state that mismatch explicitly; do not invent contents for A.\n"
            "- PhageScope: Do not claim remote access, credentials, download capability, or that an optimization 'validated PhageScope' "
            "unless phagescope tool results (e.g. action=ping or task_list) appear in the evidence with success fields.\n"
        )
        return self._append_reference_context(prompt, context)

    # ------------------------------------------------------------------ #
    #  Prompt-based (legacy) path                                         #
    # ------------------------------------------------------------------ #

    async def _think_prompt_based(
        self,
        user_query: str,
        context: Optional[Dict[str, Any]] = None,
        task_context: Optional[TaskExecutionContext] = None,
    ) -> DeepThinkResult:
        context = dict(context or {})
        thinking_steps: List[ThinkingStep] = []
        tools_used: List[str] = []

        system_prompt = self._build_system_prompt(context, task_context=task_context)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"User Query: {user_query}"}
        ]

        iteration = 0
        final_answer = ""
        fallback_used = False
        confidence = 0.0
        last_tool_cycle_signature: Optional[str] = None
        identical_tool_cycle_count = 0

        logger.info(f"Starting DeepThink for query: {user_query[:50]}...")

        while iteration < self.max_iterations:
            await self._pause_event.wait()
            if self.cancel_event and self.cancel_event.is_set():
                logger.info("DeepThink cancelled by user")
                break

            iteration += 1

            try:
                current_step = ThinkingStep(
                    iteration=iteration,
                    thought="",
                    action=None,
                    action_result=None,
                    self_correction=None,
                    timestamp=datetime.now(),
                    status="thinking"
                )

                if self.on_thinking:
                    await self._safe_callback(current_step)

                response_text = ""

                if not hasattr(self.llm_client, "stream_chat_async"):
                    raise DeepThinkProtocolError(
                        "DeepThink requires LLM client support for stream_chat_async in strict mode."
                    )

                logger.info("[DEEP_THINK] Using streaming LLM call")
                async for delta in self.llm_client.stream_chat_async(
                    prompt="", messages=messages,
                    enable_thinking=self.enable_thinking,
                    thinking_budget=self.thinking_budget,
                    on_reasoning_delta=lambda chunk: (
                        self.on_reasoning_delta(iteration, chunk)
                        if self.on_reasoning_delta else None
                    ),
                ):
                    response_text += delta
                    if self.on_thinking_delta:
                        await self._safe_delta_callback(iteration, delta)

                parsed, parse_error = self._parse_llm_response_safe(response_text)
                if parse_error:
                    logger.warning(
                        "DeepThink parse error at iteration %s: %s",
                        iteration,
                        parse_error,
                    )
                    current_step.status = "error"
                    current_step.thought = f"Protocol recovery: {parse_error}"
                    current_step.self_correction = "Requesting corrected JSON schema output."
                    thinking_steps.append(current_step)
                    if self.on_thinking:
                        await self._safe_callback(current_step)
                    messages.append({"role": "assistant", "content": response_text})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Your previous output violated protocol. "
                                "Return ONLY valid JSON with keys: thinking, action, final_answer."
                            ),
                        }
                    )
                    continue

                current_step.thought = parsed.get("thought", "")
                current_step.action = parsed.get("action_str", None)

                if parsed.get("is_final"):
                    candidate_answer = parsed.get("final_answer", "")
                    confidence = parsed.get("confidence", 0.8)
                    if self._is_valid_final_answer(candidate_answer, user_query=user_query):
                        structured_plan_outcome = self._summarize_structured_plan_outcome(
                            thinking_steps,
                            user_query=user_query,
                        )
                        final_answer = (
                            candidate_answer
                            if not structured_plan_outcome.get("required")
                            or structured_plan_outcome.get("satisfied")
                            else ""
                        )
                        if not final_answer:
                            current_step.self_correction = (
                                "Rejected the final answer because the required structured plan was not created or updated yet."
                            )
                            messages.append({"role": "assistant", "content": response_text})
                            messages.append(
                                {
                                    "role": "user",
                                    "content": self._get_structured_plan_retry_prompt(),
                                }
                            )
                    else:
                        final_answer = ""
                    current_step.status = "done"
                    thinking_steps.append(current_step)
                    if self.on_thinking:
                        await self._safe_callback(current_step)

                    # Stream final answer if callback provided
                    if self.on_final_delta and final_answer:
                        await self._stream_final_answer(final_answer)
                    if not final_answer and self._requires_structured_plan():
                        continue
                    break

                if current_step.action:
                    current_step.status = "calling_tool"
                    thinking_steps.append(current_step)
                    if self.on_thinking:
                        await self._safe_callback(current_step)

                    tool_name = parsed.get("tool_name")
                    tool_params = parsed.get("tool_params")

                    if tool_name not in self.available_tools:
                        current_step.action_result = f"Error: Tool '{tool_name}' is not available. Available: {self.available_tools}"
                    elif tool_params is not None and not isinstance(tool_params, dict):
                        current_step.action_result = f"Error: Tool params must be a dict, got {type(tool_params).__name__}"
                    else:
                        if tool_name not in tools_used:
                            tools_used.append(tool_name)
                        timeout = UnifiedToolExecutor.TOOL_TIMEOUTS.get(
                            str(tool_name),
                            self.tool_timeout,
                        )
                        attempt = 0
                        while True:
                            attempt += 1
                            try:
                                if self.on_tool_start:
                                    await self._safe_generic_callback(
                                        self.on_tool_start,
                                        str(tool_name),
                                        dict(tool_params or {}),
                                    )
                                result = await asyncio.wait_for(
                                    self.tool_executor(tool_name, tool_params or {}),
                                    timeout=timeout
                                )
                                try:
                                    current_step.action_result = json.dumps(
                                        result, ensure_ascii=False, default=str
                                    )
                                except Exception:
                                    current_step.action_result = str(result)
                                await self._emit_artifacts(str(tool_name), result, iteration)
                                callback_success, callback_error = self._normalize_tool_callback_outcome(result)
                                if self.on_tool_result:
                                    await self._safe_generic_callback(
                                        self.on_tool_result,
                                        str(tool_name),
                                        {
                                            "success": callback_success,
                                            "error": callback_error,
                                            "result": result,
                                            "summary": self._build_tool_callback_summary(result),
                                            "iteration": iteration,
                                            "attempt": attempt,
                                        },
                                    )
                                if self._should_retry_external_tool(str(tool_name), success=callback_success) and attempt <= self.MAX_EXTERNAL_TOOL_RETRIES:
                                    if self.on_tool_result:
                                        await self._safe_generic_callback(
                                            self.on_tool_result,
                                            str(tool_name),
                                            {
                                                "success": False,
                                                "error": callback_error,
                                                "summary": self._build_tool_callback_summary(result),
                                                "iteration": iteration,
                                                "attempt": attempt,
                                                "retrying": True,
                                                "retry_attempt": attempt,
                                                "max_attempts": self.MAX_EXTERNAL_TOOL_RETRIES + 1,
                                            },
                                        )
                                    continue
                                break
                            except asyncio.TimeoutError:
                                current_step.action_result = f"Error: Tool '{tool_name}' execution timed out after {timeout}s"
                                logger.warning(f"Tool {tool_name} timed out after {timeout}s")
                                should_retry = self._should_retry_external_tool(str(tool_name), success=False) and attempt <= self.MAX_EXTERNAL_TOOL_RETRIES
                                if self.on_tool_result:
                                    await self._safe_generic_callback(
                                        self.on_tool_result,
                                        str(tool_name),
                                        {
                                            "success": False,
                                            "error": "timeout",
                                            "summary": current_step.action_result,
                                            "iteration": iteration,
                                            "attempt": attempt,
                                            "retrying": should_retry,
                                            "retry_attempt": attempt if should_retry else None,
                                            "max_attempts": self.MAX_EXTERNAL_TOOL_RETRIES + 1 if should_retry else None,
                                        },
                                    )
                                if should_retry:
                                    continue
                                break
                            except Exception as e:
                                current_step.action_result = f"Error executing tool: {str(e)}"
                                logger.exception(f"Tool {tool_name} execution failed")
                                should_retry = self._should_retry_external_tool(str(tool_name), success=False) and attempt <= self.MAX_EXTERNAL_TOOL_RETRIES
                                if self.on_tool_result:
                                    await self._safe_generic_callback(
                                        self.on_tool_result,
                                        str(tool_name),
                                        {
                                            "success": False,
                                            "error": str(e),
                                            "summary": current_step.action_result,
                                            "iteration": iteration,
                                            "attempt": attempt,
                                            "retrying": should_retry,
                                            "retry_attempt": attempt if should_retry else None,
                                            "max_attempts": self.MAX_EXTERNAL_TOOL_RETRIES + 1 if should_retry else None,
                                        },
                                    )
                                if should_retry:
                                    continue
                                break

                    messages.append({"role": "assistant", "content": response_text})
                    messages.append({"role": "user", "content": f"Tool Output: {current_step.action_result}"})

                    cycle_results = [
                        {
                            "tool_name": str(tool_name or ""),
                            "tool_params": dict(tool_params or {}),
                            "tool_result_text": current_step.action_result or "",
                        }
                    ]
                    tool_cycle_signature = self._build_tool_cycle_signature(cycle_results)
                    if tool_cycle_signature and tool_cycle_signature == last_tool_cycle_signature:
                        identical_tool_cycle_count += 1
                    else:
                        last_tool_cycle_signature = tool_cycle_signature
                        identical_tool_cycle_count = 0

                    if identical_tool_cycle_count >= self.MAX_IDENTICAL_TOOL_CALL_CYCLES:
                        repeated_cycles = identical_tool_cycle_count + 1
                        current_step.status = "done"
                        current_step.self_correction = (
                            "Stopped repeated identical tool polling to avoid an unproductive loop."
                        )
                        if self.on_thinking:
                            await self._safe_callback(current_step)
                        final_answer = self._build_repetition_stop_answer(
                            tool_results=cycle_results,
                            repeated_cycles=repeated_cycles,
                        )
                        confidence = max(
                            confidence,
                            0.75 if str(tool_name or "").strip().lower() == "phagescope" else 0.5,
                        )
                        if self.on_final_delta and final_answer:
                            await self._stream_final_answer(final_answer)
                        break

                    current_step.status = "analyzing"
                    if self.on_thinking:
                        await self._safe_callback(current_step)

                else:
                    thinking_steps.append(current_step)
                    if self.on_thinking:
                        await self._safe_callback(current_step)
                    messages.append({"role": "assistant", "content": response_text})
                    messages.append({"role": "user", "content": self._get_next_step_prompt(iteration)})

            except Exception as e:
                logger.exception("Error in deep thinking loop")
                current_step.status = "error"
                current_step.thought = f"Error: {str(e)}"
                thinking_steps.append(current_step)
                if self.on_thinking:
                    await self._safe_callback(current_step)
                messages.append(
                    {
                        "role": "user",
                        "content": "Continue with a robust fallback and provide valid JSON only.",
                    }
                )
                continue

            if self._skip_current_step:
                self._skip_current_step = False
                messages.append(
                    {
                        "role": "user",
                        "content": "Skip current branch and continue with the next reasoning step.",
                    }
                )

        if not final_answer and thinking_steps:
            logger.info("[DEEP_THINK] Iterations exhausted, requesting strict final conclusion")

            conclusion_prompt = """You have reached the thinking limit. Based on the information you already have,
you MUST now provide a final answer. Synthesize what is most relevant into a direct, appropriately scoped response.

Respond with ONLY a JSON object:
{
  "thinking": "I've gathered the following key information: [summarize key findings]",
  "action": null,
  "final_answer": {"answer": "Your final answer based on the gathered information", "confidence": 0.7}
}"""

            messages.append({"role": "user", "content": conclusion_prompt})

            try:
                if not hasattr(self.llm_client, "stream_chat_async"):
                    raise DeepThinkProtocolError(
                        "DeepThink requires stream_chat_async for forced conclusion in strict mode."
                    )

                response_text = ""
                async for delta in self.llm_client.stream_chat_async(
                    prompt="", messages=messages,
                    enable_thinking=self.enable_thinking,
                    thinking_budget=self.thinking_budget,
                    on_reasoning_delta=lambda chunk: (
                        self.on_reasoning_delta(iteration + 1, chunk)
                        if self.on_reasoning_delta else None
                    ),
                ):
                    response_text += delta
                    if self.on_thinking_delta:
                        await self._safe_delta_callback(iteration + 1, delta)

                parsed, parse_error = self._parse_llm_response_safe(response_text)
                if parse_error:
                    logger.warning("Forced conclusion parse fallback triggered: %s", parse_error)
                    parsed = {}
                if parsed.get("is_final"):
                    candidate_answer = parsed.get("final_answer", "")
                    confidence = parsed.get("confidence", 0.7)
                    if self._is_valid_final_answer(candidate_answer, user_query=user_query):
                        structured_plan_outcome = self._summarize_structured_plan_outcome(
                            thinking_steps,
                            user_query=user_query,
                        )
                        final_answer = (
                            candidate_answer
                            if not structured_plan_outcome.get("required")
                            or structured_plan_outcome.get("satisfied")
                            else ""
                        )
                    else:
                        final_answer = ""

                    # Stream final answer
                    if self.on_final_delta and final_answer:
                        await self._stream_final_answer(final_answer)
                else:
                    fallback_used = True
                    final_answer = await self._fallback_answer_from_steps(thinking_steps, user_query)
                    confidence = 0.5
                    if self.on_final_delta and final_answer:
                        await self._stream_final_answer(final_answer)
            except Exception as e:
                logger.exception("Failed to generate strict forced conclusion")
                fallback_used = True
                final_answer = await self._fallback_answer_from_steps(thinking_steps, user_query)
                confidence = 0.4

        if final_answer and not self._is_valid_final_answer(final_answer, user_query=user_query):
            final_answer = ""

        # Forced synthesis before generic fallback (prompt-based path)
        if not final_answer and thinking_steps:
            logger.info("[DEEP_THINK] Attempting forced synthesis (prompt-based path)")
            final_answer = await self._forced_synthesis_from_steps(thinking_steps, user_query, messages)
            if final_answer:
                fallback_used = True
                confidence = max(confidence, 0.5)
                if self.on_final_delta:
                    await self._stream_final_answer(final_answer)

        if not final_answer:
            fallback_used = True
            final_answer = await self._fallback_answer_from_steps(thinking_steps, user_query)
            confidence = max(confidence, 0.3)

        tool_failures = self._collect_tool_failures_from_steps(thinking_steps)
        search_verified = self._search_verified_from_steps(thinking_steps)
        final_answer = self._apply_external_search_notice(
            final_answer,
            user_query=user_query,
            tool_failures=tool_failures,
            search_verified=search_verified,
        )
        execute_truth_answer = self._apply_execute_failure_truth_barrier(
            final_answer,
            user_query=user_query,
            steps=thinking_steps,
        )
        if execute_truth_answer != final_answer:
            fallback_used = True
        final_answer = execute_truth_answer
        structured_plan_outcome = self._summarize_structured_plan_outcome(
            thinking_steps,
            user_query=user_query,
        )
        final_answer = self._ensure_structured_plan_notice(
            final_answer,
            outcome=structured_plan_outcome,
            user_query=user_query,
        )
        final_answer = sanitize_professional_response_text(final_answer)

        try:
            summary = await self._generate_summary(thinking_steps, user_query)
        except Exception:
            summary = _default_deepthink_summary(user_query)

        return DeepThinkResult(
            final_answer=final_answer,
            thinking_steps=thinking_steps,
            total_iterations=iteration,
            tools_used=tools_used,
            confidence=confidence,
            thinking_summary=summary,
            tool_failures=tool_failures,
            search_verified=search_verified,
            fallback_used=fallback_used,
            structured_plan_required=bool(structured_plan_outcome.get("required")),
            structured_plan_satisfied=bool(structured_plan_outcome.get("satisfied")),
            structured_plan_state=structured_plan_outcome.get("state"),
            structured_plan_message=structured_plan_outcome.get("message"),
            structured_plan_plan_id=structured_plan_outcome.get("plan_id"),
            structured_plan_title=structured_plan_outcome.get("plan_title"),
            structured_plan_operation=structured_plan_outcome.get("operation"),
        )

    async def _safe_callback(self, step: ThinkingStep):
        if self.on_thinking:
            try:
                if asyncio.iscoroutinefunction(self.on_thinking):
                    await self.on_thinking(step)
                else:
                    self.on_thinking(step)
            except Exception as e:
                logger.error(f"Error in on_thinking callback: {e}")

    async def _safe_generic_callback(self, callback: Callable[..., Any], *args: Any) -> None:
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(*args)
            else:
                ret = callback(*args)
                if asyncio.iscoroutine(ret):
                    await ret
        except Exception as e:
            logger.error("Error in callback: %s", e)

    async def _safe_delta_callback(self, iteration: int, delta: str):
        if self.on_thinking_delta:
            try:
                # Truncate very long deltas (e.g., FASTA file contents in JSON)
                # to prevent UI from being overwhelmed
                MAX_DELTA_LENGTH = 2000
                if len(delta) > MAX_DELTA_LENGTH:
                    # Find a reasonable truncation point
                    truncated = delta[:MAX_DELTA_LENGTH]
                    # Try to truncate at a newline or space for cleaner display
                    last_newline = truncated.rfind('\\n')
                    last_space = truncated.rfind(' ')
                    cut_point = max(last_newline, last_space, MAX_DELTA_LENGTH - 200)
                    if cut_point > MAX_DELTA_LENGTH - 500:
                        truncated = delta[:cut_point]
                    delta = truncated + f"... [truncated, {len(delta) - len(truncated)} chars hidden]"

                if asyncio.iscoroutinefunction(self.on_thinking_delta):
                    await self.on_thinking_delta(iteration, delta)
                else:
                    self.on_thinking_delta(iteration, delta)
            except Exception as e:
                logger.error(f"Error in on_thinking_delta callback: {e}")

    async def _safe_final_delta_callback(self, delta: str):
        if self.on_final_delta:
            try:
                if asyncio.iscoroutinefunction(self.on_final_delta):
                    await self.on_final_delta(delta)
                else:
                    self.on_final_delta(delta)
            except Exception as e:
                logger.error(f"Error in on_final_delta callback: {e}")

    @staticmethod
    def _normalize_tool_callback_outcome(result: Any) -> tuple[bool, Optional[str]]:
        if isinstance(result, dict):
            if "success" in result:
                success = bool(result.get("success"))
                error_val = result.get("error")
                error = str(error_val).strip() if error_val is not None else None
                return success, error
            nested = result.get("result")
            if isinstance(nested, dict) and nested.get("success") is False:
                nested_error = nested.get("error")
                if nested_error is not None:
                    return False, str(nested_error)
                return False, None
        return True, None

    def _extract_artifact_paths(self, tool_name: str, result: Any) -> List[str]:
        """Extract file paths from tool results that look like produced artifacts."""
        if tool_name == "terminal_session":
            if not isinstance(result, dict):
                return []
            verification_state = str(result.get("verification_state") or "").strip().lower()
            if verification_state != "verified_success":
                return []
            explicit_paths = result.get("artifact_paths")
            if isinstance(explicit_paths, list):
                cleaned: List[str] = []
                for item in explicit_paths:
                    if isinstance(item, str) and item.strip() and not self._is_internal_artifact_path(item):
                        cleaned.append(item.strip())
                return list(dict.fromkeys(cleaned))
            return []
        text = str(result) if result is not None else ""
        if not text:
            return []
        paths: List[str] = []
        for m in self.ARTIFACT_PATH_RE.finditer(text):
            candidate = m.group(1)
            if not self._is_internal_artifact_path(candidate):
                paths.append(candidate)
        for m in self.BARE_PATH_RE.finditer(text):
            candidate = m.group(1)
            if candidate not in paths and not self._is_internal_artifact_path(candidate):
                paths.append(candidate)
        return list(dict.fromkeys(paths))

    @staticmethod
    def _is_internal_artifact_path(path: str) -> bool:
        normalized = "/" + str(path or "").strip().replace("\\", "/").lstrip("/")
        if not normalized or normalized == "/":
            return False
        basename = normalized.rsplit("/", 1)[-1].lower()
        if basename in _INTERNAL_ARTIFACT_FILENAMES and "/tool_outputs/" in normalized.lower():
            return True
        if normalized.lower().endswith("/deliverables/manifest_latest.json"):
            return True
        return bool(_INTERNAL_TOOL_OUTPUT_RE.search(normalized))

    async def _emit_artifacts(self, tool_name: str, result: Any, iteration: int) -> None:
        if not self.on_artifact:
            return
        paths = self._extract_artifact_paths(tool_name, result)
        for path in paths:
            ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
            await self._safe_generic_callback(
                self.on_artifact,
                {
                    "path": path,
                    "display_name": path.rsplit("/", 1)[-1] if "/" in path else path,
                    "extension": ext,
                    "source_tool": tool_name,
                    "iteration": iteration,
                },
            )

    async def _execute_native_tool_call(
        self,
        tc: Any,
        iteration: int,
        index: int,
    ) -> Dict[str, Any]:
        from tool_box.context import ToolContext

        tool_name = str(getattr(tc, "name", "") or "")
        tool_params = getattr(tc, "arguments", {}) or {}
        tool_call_id = str(getattr(tc, "id", "") or f"native_{iteration}_{index}")
        timeout = UnifiedToolExecutor.TOOL_TIMEOUTS.get(tool_name, self.tool_timeout)

        async def _progress_bridge(data: Dict[str, Any]) -> None:
            if self.on_tool_progress:
                await self._safe_generic_callback(
                    self.on_tool_progress, tool_name, data,
                )

        tool_ctx = ToolContext(
            on_progress=_progress_bridge,
            plan_id=self._current_plan_id(),
            session_id=str(self.request_profile.get("session_id") or "").strip() or None,
        )

        if self.on_tool_start and tool_name:
            await self._safe_generic_callback(self.on_tool_start, tool_name, tool_params)

        if tool_name not in self.available_tools:
            error_payload = {
                "success": False,
                "error": f"tool_not_available:{tool_name}",
                "summary": f"Tool '{tool_name}' is not available.",
                "iteration": iteration,
            }
            if self.on_tool_result and tool_name:
                await self._safe_generic_callback(self.on_tool_result, tool_name, error_payload)
            tool_result_text = json.dumps(error_payload, ensure_ascii=False)
            return {
                "index": index,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "tool_params": tool_params,
                "tool_result": error_payload,
                "tool_result_text": tool_result_text,
                "evidence": [],
            }

        params_with_ctx = {**tool_params, "tool_context": tool_ctx}
        if tool_name == "code_executor":
            params_with_ctx["auto_fix"] = False
        attempt = 0
        while True:
            attempt += 1
            try:
                tool_result = await asyncio.wait_for(
                    self.tool_executor(tool_name, params_with_ctx),
                    timeout=timeout,
                )
                callback_success, callback_error = self._normalize_tool_callback_outcome(tool_result)
                callback_payload = {
                    "success": callback_success,
                    "error": callback_error,
                    "result": tool_result,
                    "summary": self._build_tool_callback_summary(tool_result),
                    "iteration": iteration,
                    "attempt": attempt,
                }
                if not callback_success:
                    logger.warning(
                        "[DEEP_THINK_NATIVE] Tool returned success=false: tool=%s tool_call_id=%s summary=%s error=%s",
                        tool_name,
                        tool_call_id,
                        self._clip_log_text(callback_payload.get("summary"), limit=360),
                        self._clip_log_text(callback_error, limit=240),
                    )
                    if self._should_retry_external_tool(tool_name, success=False) and attempt <= self.MAX_EXTERNAL_TOOL_RETRIES:
                        if self.on_tool_result:
                            await self._safe_generic_callback(
                                self.on_tool_result,
                                tool_name,
                                {
                                    **callback_payload,
                                    "retrying": True,
                                    "retry_attempt": attempt,
                                    "max_attempts": self.MAX_EXTERNAL_TOOL_RETRIES + 1,
                                },
                            )
                        if self.on_tool_start:
                            await self._safe_generic_callback(self.on_tool_start, tool_name, tool_params)
                        continue
                if self.on_tool_result:
                    await self._safe_generic_callback(self.on_tool_result, tool_name, callback_payload)
                await self._emit_artifacts(tool_name, tool_result, iteration)
                tool_result_text = self._build_tool_result_text_for_llm(
                    tool_name=tool_name,
                    result=tool_result,
                    success=callback_success,
                    error=callback_error,
                )
                evidence = self._extract_evidence(tool_name, tool_params, tool_result)
                return {
                    "index": index,
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "tool_params": tool_params,
                    "tool_result": tool_result,
                    "tool_result_text": tool_result_text,
                    "evidence": evidence,
                }
            except asyncio.TimeoutError:
                timeout_payload = {
                    "success": False,
                    "tool": tool_name,
                    "error": "timeout",
                    "summary": f"Tool '{tool_name}' timed out after {timeout}s",
                }
                should_retry = self._should_retry_external_tool(tool_name, success=False) and attempt <= self.MAX_EXTERNAL_TOOL_RETRIES
                if self.on_tool_result:
                    await self._safe_generic_callback(
                        self.on_tool_result,
                        tool_name,
                        {
                            "success": False,
                            "error": "timeout",
                            "summary": timeout_payload["summary"],
                            "iteration": iteration,
                            "attempt": attempt,
                            "retrying": should_retry,
                            "retry_attempt": attempt if should_retry else None,
                            "max_attempts": self.MAX_EXTERNAL_TOOL_RETRIES + 1 if should_retry else None,
                        },
                    )
                if should_retry:
                    if self.on_tool_start:
                        await self._safe_generic_callback(self.on_tool_start, tool_name, tool_params)
                    continue
                return {
                    "index": index,
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "tool_params": tool_params,
                    "tool_result": timeout_payload,
                    "tool_result_text": json.dumps(timeout_payload, ensure_ascii=False),
                    "evidence": [],
                }
            except Exception as exc:
                logger.exception(
                    "Tool %s failed (tool_call_id=%s, params=%s)",
                    tool_name,
                    tool_call_id,
                    self._sanitize_tool_params_for_log(tool_params),
                )
                failure_payload = {
                    "success": False,
                    "tool": tool_name,
                    "error": str(exc),
                    "summary": f"Error executing tool: {exc}",
                }
                should_retry = self._should_retry_external_tool(tool_name, success=False) and attempt <= self.MAX_EXTERNAL_TOOL_RETRIES
                if self.on_tool_result:
                    await self._safe_generic_callback(
                        self.on_tool_result,
                        tool_name,
                        {
                            "success": False,
                            "error": str(exc),
                            "summary": failure_payload["summary"],
                            "iteration": iteration,
                            "attempt": attempt,
                            "retrying": should_retry,
                            "retry_attempt": attempt if should_retry else None,
                            "max_attempts": self.MAX_EXTERNAL_TOOL_RETRIES + 1 if should_retry else None,
                        },
                    )
                if should_retry:
                    if self.on_tool_start:
                        await self._safe_generic_callback(self.on_tool_start, tool_name, tool_params)
                    continue
                return {
                    "index": index,
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "tool_params": tool_params,
                    "tool_result": failure_payload,
                    "tool_result_text": json.dumps(failure_payload, ensure_ascii=False),
                    "evidence": [],
                }

    def _extract_evidence(
        self,
        tool_name: str,
        tool_params: Dict[str, Any],
        tool_result: Any,
    ) -> List[Dict[str, str]]:
        text = str(tool_result or "")
        evidence: List[Dict[str, str]] = []

        for path in self._extract_artifact_paths(tool_name, tool_result):
            evidence.append(
                {
                    "type": "file",
                    "title": "Generated file",
                    "ref": path,
                    "snippet": f"{tool_name} produced {path}",
                }
            )
        for m in self.URL_RE.finditer(text):
            url = m.group(0)
            evidence.append(
                {
                    "type": "url",
                    "title": "External source",
                    "ref": url,
                    "snippet": f"{tool_name} referenced {url}",
                }
            )
        task_id = None
        job_id = None
        if isinstance(tool_result, dict):
            for key in ("taskid", "task_id", "remote_taskid", "remote_task_id"):
                val = tool_result.get(key)
                if isinstance(val, (str, int)) and str(val).strip():
                    task_id = str(val).strip()
                    break
            job_val = tool_result.get("job_id")
            if isinstance(job_val, (str, int)) and str(job_val).strip():
                job_id = str(job_val).strip()
        if task_id:
            evidence.append(
                {
                    "type": "task",
                    "title": "Background task",
                    "ref": task_id,
                    "snippet": f"{tool_name} created task {task_id}",
                }
            )
        if job_id and job_id != task_id:
            evidence.append(
                {
                    "type": "job",
                    "title": "Background job",
                    "ref": job_id,
                    "snippet": f"{tool_name} created job {job_id}",
                }
            )
        if not evidence:
            snippet = (text or "").strip().replace("\n", " ")
            if len(snippet) > 240:
                snippet = snippet[:240] + "..."
            if snippet:
                evidence.append(
                    {
                        "type": "output",
                        "title": "Tool output",
                        "ref": tool_name,
                        "snippet": snippet,
                    }
                )
        return evidence[:8]

    @staticmethod
    def _build_tool_callback_summary(result: Any) -> str:
        if isinstance(result, dict):
            parts: List[str] = []
            if "summary" in result:
                parts.append(str(result["summary"])[:500])
            elif "error" in result and result.get("error"):
                parts.append(str(result.get("error"))[:500])
            # Surface partial completion signals so LLM is aware
            if result.get("partial_completion_suspected"):
                ratio = result.get("partial_ratio", "unknown")
                parts.append(f"⚠️ PARTIAL COMPLETION SUSPECTED (ratio: {ratio}). Verify all expected outputs exist.")
            output_warnings = result.get("output_warnings")
            if isinstance(output_warnings, list) and output_warnings:
                parts.append(f"⚠️ {len(output_warnings)} warning(s) in output: {output_warnings[0][:150]}")
            if parts:
                return "; ".join(parts)[:600]
        return str(result)[:600]

    @classmethod
    def _build_tool_result_text_for_llm(
        cls,
        *,
        tool_name: str,
        result: Any,
        success: bool,
        error: Any,
    ) -> str:
        payload = {
            "success": success,
            "tool": tool_name,
            "result": result,
            "error": error,
        }
        raw_text = json.dumps(payload, ensure_ascii=False, default=str)
        if len(raw_text) <= cls.MAX_TOOL_RESULT_TEXT_CHARS:
            return raw_text

        compact_result = cls._compact_tool_result_for_llm(tool_name, result)
        if compact_result is None:
            return raw_text

        compact_payload = {
            "success": success,
            "tool": tool_name,
            "result": compact_result,
            "error": error,
        }
        compact_text = json.dumps(compact_payload, ensure_ascii=False, default=str)
        logger.info(
            "[DEEP_THINK_NATIVE] Compacted tool result for llm context: tool=%s raw_chars=%s compact_chars=%s",
            tool_name,
            len(raw_text),
            len(compact_text),
        )
        return compact_text

    @classmethod
    def _compact_tool_result_for_llm(
        cls, tool_name: str, result: Any
    ) -> Optional[Dict[str, Any]]:
        if str(tool_name or "").strip().lower() == "file_operations":
            return cls._compact_file_operations_result_for_llm(result)
        return None

    @classmethod
    def _compact_file_operations_result_for_llm(
        cls, result: Any
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(result, dict):
            return None
        if str(result.get("operation") or "").strip().lower() != "list":
            return None

        items = result.get("items")
        if not isinstance(items, list):
            return None

        count_raw = result.get("count")
        try:
            total_count = int(count_raw)
        except Exception:
            total_count = len(items)

        file_count = 0
        directory_count = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip().lower()
            if item_type == "file":
                file_count += 1
            elif item_type == "directory":
                directory_count += 1

        path = str(result.get("path") or "").strip()
        preview_limit = min(len(items), cls.MAX_FILE_OPERATION_LIST_SAMPLE_ITEMS)
        compact_result: Dict[str, Any] = {}

        while True:
            sample_items: List[Dict[str, Any]] = []
            for item in items[:preview_limit]:
                if not isinstance(item, dict):
                    continue
                sample_item: Dict[str, Any] = {
                    "name": str(item.get("name") or ""),
                    "type": str(item.get("type") or ""),
                }
                size_value = item.get("size")
                if isinstance(size_value, (int, float)):
                    sample_item["size"] = int(size_value)
                sample_items.append(sample_item)

            compact_result = {
                "operation": "list",
                "path": path,
                "success": bool(result.get("success", True)),
                "count": total_count,
                "files_count": file_count,
                "directories_count": directory_count,
                "sample_items": sample_items,
                "omitted_items": max(0, total_count - len(sample_items)),
                "llm_compacted": True,
                "summary": (
                    f"Listed {total_count} items under {path or '.'} "
                    f"({file_count} files, {directory_count} directories). "
                    f"Showing the first {len(sample_items)} item(s) only because the full directory listing is too large for LLM context."
                ),
            }
            compact_text = json.dumps(compact_result, ensure_ascii=False, default=str)
            if len(compact_text) <= cls.MAX_TOOL_RESULT_TEXT_CHARS or preview_limit == 0:
                return compact_result
            if preview_limit <= 5:
                preview_limit = 0
            else:
                preview_limit //= 2

    @staticmethod
    def _append_tool_cycle_messages(
        *,
        messages: List[Dict[str, Any]],
        tool_results: List[Dict[str, Any]],
        assistant_content: str,
        current_step: "ThinkingStep",
    ) -> None:
        """Build assistant + tool messages from a tool execution cycle and update the step."""
        assistant_msg: Dict[str, Any] = {"role": "assistant", "content": assistant_content}
        assistant_msg["tool_calls"] = [
            {
                "id": item["tool_call_id"],
                "type": "function",
                "function": {
                    "name": item["tool_name"],
                    "arguments": json.dumps(item.get("tool_params") or {}, ensure_ascii=False),
                },
            }
            for item in tool_results
        ]
        messages.append(assistant_msg)
        for item in tool_results:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item["tool_call_id"],
                    "content": item["tool_result_text"],
                }
            )
        per_tool_text = [
            f"[{item['tool_name']}] {item['tool_result_text']}"
            for item in tool_results
        ]
        current_step.action_result = "\n\n".join(per_tool_text)
        merged_evidence: List[Dict[str, str]] = []
        for item in tool_results:
            merged_evidence.extend(item.get("evidence") or [])
        current_step.evidence = merged_evidence
        current_step.finished_at = datetime.now()

    @staticmethod
    def _contains_tool(tool_results: List[Dict[str, Any]], tool_name: str) -> bool:
        for item in tool_results:
            if str(item.get("tool_name") or "").strip().lower() == tool_name:
                return True
        return False

    @classmethod
    def _build_tool_cycle_signature(cls, tool_results: List[Dict[str, Any]]) -> str:
        signature_parts: List[str] = []
        for item in tool_results:
            tool_name = str(item.get("tool_name") or "").strip().lower()
            tool_params = item.get("tool_params") or {}
            result_marker = cls._extract_tool_result_marker(tool_name, item.get("tool_result_text"))
            try:
                params_text = json.dumps(tool_params, ensure_ascii=False, sort_keys=True, default=str)
            except Exception:
                params_text = str(tool_params)
            signature_parts.append(f"{tool_name}|{params_text}|{result_marker}")
        raw = "\n".join(signature_parts)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    @classmethod
    def _extract_tool_result_marker(cls, tool_name: str, tool_result_text: Any) -> str:
        raw_text = str(tool_result_text or "")
        if tool_name == "phagescope":
            state = cls._extract_phagescope_state(raw_text)
            if state:
                return (
                    f"task={state.get('task_id')};status={state.get('status')};"
                    f"task_status={state.get('task_status')};progress={state.get('progress')};"
                    f"waiting={state.get('waiting')};running={state.get('running')};failed={state.get('failed')}"
                )
        normalized = cls._normalize_marker_text(raw_text)
        return hashlib.sha1(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_marker_text(raw_text: str) -> str:
        text = raw_text or ""
        # Remove timestamp-like values to make stability detection resilient.
        text = re.sub(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?", "<ts>", text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 600:
            text = text[:600]
        return text

    @classmethod
    def _extract_phagescope_state(cls, tool_result_text: str) -> Optional[Dict[str, Any]]:
        try:
            payload = json.loads(tool_result_text)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        result = payload.get("result")
        if not isinstance(result, dict):
            # Prompt-based DeepThink may serialize tool payload directly instead of
            # wrapping under {"result": ...}. Accept that shape as well.
            if "data" in payload or "action" in payload or "status_code" in payload:
                result = payload
            else:
                return None

        data = result.get("data")
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, dict):
            return None

        task_id = results.get("id") or result.get("taskid") or result.get("task_id") or ""
        status = str(results.get("status") or "").strip()
        task_status = ""
        progress = ""
        waiting = 0
        running = 0
        failed = 0
        completed = 0
        total = 0

        detail_raw = results.get("task_detail")
        detail: Optional[Dict[str, Any]] = None
        if isinstance(detail_raw, dict):
            detail = detail_raw
        elif isinstance(detail_raw, str):
            stripped = detail_raw.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, dict):
                        detail = parsed
                except Exception:
                    detail = None

        if isinstance(detail, dict):
            task_status = str(detail.get("task_status") or "").strip()
            queue = detail.get("task_que")
            if isinstance(queue, list):
                total = len(queue)
                for module_item in queue:
                    if not isinstance(module_item, dict):
                        continue
                    module_status = str(module_item.get("module_satus") or "").strip().lower()
                    if module_status == "completed":
                        completed += 1
                    elif module_status in {"waiting", "wait"}:
                        waiting += 1
                    elif module_status in {"running", "create", "queued"}:
                        running += 1
                    elif module_status in {"failed", "error"}:
                        failed += 1
            if total > 0:
                progress = f"{completed}/{total}"

        return {
            "task_id": str(task_id),
            "status": status,
            "task_status": task_status,
            "progress": progress,
            "waiting": waiting,
            "running": running,
            "failed": failed,
        }

    @classmethod
    def _build_repetition_stop_answer(
        cls,
        tool_results: List[Dict[str, Any]],
        repeated_cycles: int,
    ) -> str:
        phagescope_state: Optional[Dict[str, Any]] = None
        for item in tool_results:
            if str(item.get("tool_name") or "").strip().lower() != "phagescope":
                continue
            phagescope_state = cls._extract_phagescope_state(str(item.get("tool_result_text") or ""))
            if phagescope_state:
                break

        if phagescope_state:
            task_id = phagescope_state.get("task_id") or "unknown"
            status = phagescope_state.get("status") or "unknown"
            task_status = phagescope_state.get("task_status") or "unknown"
            progress = phagescope_state.get("progress") or "unknown"
            return (
                f"PhageScope task {task_id} is still unchanged after {repeated_cycles} polling cycles "
                f"(status={status}, task_status={task_status}, module_progress={progress}).\n\n"
                "DeepThink stopped active polling to avoid an infinite loop. "
                "Please retry status check later, or continue once the remote task state changes."
            )

        return (
            "Tool outputs remained unchanged across repeated cycles, so DeepThink stopped active polling "
            f"after {repeated_cycles} repeats to avoid an infinite loop. "
            "Please retry later or provide new constraints."
        )

    @staticmethod
    def _clip_log_text(value: Any, *, limit: int = 400) -> str:
        text = " ".join(str(value or "").split()).strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)] + "..."

    @classmethod
    def _sanitize_tool_params_for_log(cls, params: Any) -> str:
        redact_tokens = ("password", "passwd", "secret", "token", "api_key", "apikey", "authorization")

        def _sanitize(value: Any, depth: int = 0) -> Any:
            if depth >= 4:
                return "<truncated>"
            if isinstance(value, dict):
                sanitized: Dict[str, Any] = {}
                for key, item in value.items():
                    key_text = str(key)
                    key_lower = key_text.lower()
                    if any(token in key_lower for token in redact_tokens):
                        sanitized[key_text] = "<redacted>"
                        continue
                    sanitized[key_text] = _sanitize(item, depth + 1)
                return sanitized
            if isinstance(value, list):
                return [_sanitize(item, depth + 1) for item in value[:20]]
            if isinstance(value, tuple):
                return [_sanitize(item, depth + 1) for item in value[:20]]
            if isinstance(value, str):
                return cls._clip_log_text(value, limit=240)
            return value

        try:
            sanitized_params = _sanitize(params)
            raw = json.dumps(sanitized_params, ensure_ascii=False, default=str)
        except Exception:
            raw = str(params)
        return cls._clip_log_text(raw, limit=800)

    @classmethod
    def _chunk_final_answer(cls, text: str) -> List[str]:
        text = text or ""
        if not text:
            return []

        chunks: List[str] = []
        buffer: List[str] = []
        max_chars = max(8, int(cls.FINAL_STREAM_CHUNK_CHARS))
        split_chars = {".", "!", "?", "\n", ",", ";", ":", "，", "。", "！", "？"}

        for ch in text:
            buffer.append(ch)
            if len(buffer) >= max_chars or (ch in split_chars and len(buffer) >= max_chars // 2):
                chunks.append("".join(buffer))
                buffer = []

        if buffer:
            chunks.append("".join(buffer))
        return chunks

    async def _stream_final_answer(self, final_answer: str) -> None:
        if not self.on_final_delta or not final_answer:
            return
        cleaned_answer = sanitize_professional_response_text(final_answer)
        for chunk in self._chunk_final_answer(cleaned_answer):
            await self._safe_final_delta_callback(chunk)
            if self.FINAL_STREAM_DELAY_SEC > 0:
                await asyncio.sleep(self.FINAL_STREAM_DELAY_SEC)

    def _build_system_prompt(
        self,
        context: Optional[Dict[str, Any]] = None,
        task_context: Optional[TaskExecutionContext] = None,
    ) -> str:
        """Construct the system prompt for DeepThink, task-aware when available."""
        # Build detailed tool descriptions
        tool_descriptions = {
            "sequence_fetch": (
                "Deterministic accession-to-FASTA downloader. "
                "Use for FASTA downloads by accession IDs before analysis. "
                "Params: {\"accession\": \"NC_001416.1\"} or "
                "{\"accessions\": [\"NC_001416.1\", \"NC_001417.1\"], "
                "\"database\": \"nuccore|protein\", \"format\": \"fasta\"}. "
                "Do not use code_executor as fallback when sequence_fetch fails."
            ),
            "code_executor": "Execute Python/shell code. FALLBACK TOOL: Use this ONLY when bio_tools cannot handle the task (e.g., custom analysis scripts, complex data processing). For FASTA/FASTQ sequence stats or standard bioinformatics tasks, ALWAYS try bio_tools first. For local CSV/TSV overview/schema/count requests, prefer result_interpreter profile first. Params: {\"task\": \"description\"}",
            "web_search": "Search the internet for information. USE THIS ONLY for web-based queries, NOT for local files. For broad comparisons, prefer focused parallel subqueries with Params: {\"query\": \"original request\", \"queries\": [\"focused query 1\", \"focused query 2\"]}.",
            "graph_rag": "Query knowledge graph for structured information. Params: {\"query\": \"your question\", \"mode\": \"global|local|hybrid\"}",
            "file_operations": "File system operations: list directories, read/write files, copy/move/delete. USE THIS for quick directory listing or file reading. For a bound execute_task request, read/list/exists/info are inspection-only and do not count as task completion. Params: {\"operation\": \"list|read|write|copy|move|delete\", \"path\": \"/path\"}",
            "document_reader": (
                "Read local documents (.docx, .pdf, .txt, .md). For .csv/.tsv, this tool "
                "returns a built-in preview (headers + sample rows) — use it for quick inspection; "
                "for aggregation, row counts on huge files, or plots use code_executor. "
                "For a bound execute_task request, this is an inspection tool, not a substitute for actually executing the task. "
                "Params: {\"operation\": \"read_any|read_pdf|read_text\", \"file_path\": \"/abs/path\"}"
            ),
            "vision_reader": "Read PDFs and images using vision model. Use for visual OCR/figures/equations, not for DOCX. For a bound execute_task request, this is inspection-only and should not replace the actual execution tool. Params: {\"operation\": \"read_pdf|read_image|ocr_page\", \"file_path\": \"/path/to/file\"}",
            "bio_tools": (
                "PREFERRED for bioinformatics: Execute Docker-based tools for FASTA/FASTQ/sequence analysis. "
                "Example: {\"tool_name\": \"seqkit\", \"operation\": \"stats\", \"input_file\": \"/absolute/path/to/file.fasta\"}. "
                "For inline sequence content, pass sequence_text instead of input_file. "
                "NOTE: input_file SHOULD be absolute path. "
                f"Available tools (synced from tools_config.json): {', '.join(_BIO_TOOLS_NAMES)}. "
                "Use operation='help' first for exact params and prefer operations verified in bio_tool_list.md. "
                "Background policy: use background=true ONLY for long-running bio_tools operations when "
                "the current turn does not require immediate result-dependent reasoning. "
                "Keep short/interactive checks synchronous."
            ),
            "phagescope": """PhageScope cloud platform for phage genome analysis.
IMPORTANT: This is an ASYNC service - tasks run remotely and take minutes to hours.

Connectivity / access checks (user asks to test PhageScope, verify remote connectivity, or confirm download/API):
- FIRST call action=ping (optional base_url only; add token only if the user explicitly provided one). Never use file_operations or local directory listing as a substitute for PhageScope connectivity.
- If ping succeeds and account-scoped verification is needed, use task_list with userid.
- Do not ask for a mandatory "API Token from the user center"; documented flows use `userid`. The tool's `token` param is optional and usually omitted.

Workflow:
1. submit: Submit sequences → Returns taskid immediately (DO NOT wait)
2. task_list: Check all your submitted tasks
3. task_detail: Check specific task status
4. result: Get results ONLY when task is COMPLETED
5. save_all: After Success, use this to write the full local bundle (folders + summary.json). Do not equate `result`/JSON with a complete on-disk package.
6. Batch: `batch_submit` (phage_ids + modulelist; strategy multi_one_task or per_strain) writes a manifest; after Success use `batch_reconcile` (batch_id) to find missing accessions vs phage rows; use `batch_retry` (batch_id) to re-submit missing ids one strain per task. Prefer these over memorizing taskids.

After submit, stop PhageScope result retrieval in this turn unless user explicitly asks to query status only.
After submit, ALWAYS tell user with 3 parts:
- Completed now: submit + taskid
- Running in background: current status/module progress if known
- Next step: refresh status later, then fetch result/save_all/download after completion
DO NOT use wait=True, it will block too long.

Parameter rules (CRITICAL):
- Use `phageid` or `phageids`; do NOT use `sequence` for accession IDs.
- `submit` requires `userid` + `modulelist` + `phageid/phageids`.
- `modulelist` for `submit` must contain real submit modules only. Do NOT put result/output names like `proteins`, `phage_detail`, `phagefasta`, or `tree` into `modulelist`. If protein annotations are needed, request `annotation` and later fetch `result_kind=proteins` or use `save_all`.
- `input_check` requires `phageid/phageids`.
- `result` requires `taskid` + `result_kind` (quality/proteins/phage_detail/modules/tree/phagefasta).
- `taskid` must be the numeric remote task id (e.g., 37468), not a local job id like `act_xxx`.

Params: {"action": "submit|task_list|task_detail|result|save_all|download|batch_submit|batch_reconcile|batch_retry", "userid": "...", "phageid": "...", "phageids": "...", "phage_ids": [...], "batch_id": "...", "taskid": "...", "result_kind": "..."}""",
            "deeppl": """DeepPL lifecycle prediction tool (DNABERT-based).

Actions:
1. help: inspect usage, defaults, and environment requirements.
2. predict: run lifecycle prediction.
3. job_status: query a background DeepPL job.

Predict rules:
- Provide exactly one of input_file or sequence_text.
- Prefer execution_mode='local' unless user clearly requires remote.
- For split CPU/GPU remote servers, set remote_profile='gpu' or 'cpu' explicitly.
- model_path is required (or configured via env).
- Optional background=true for long-running prediction; then query via job_status.

Output fields to report:
- predicted_label: lysogenic|lytic
- predicted_lifestyle: temperate|virulent
- positive_window_fraction and thresholds""",
            "result_interpreter": """Data analysis and result interpretation tool.
Can inspect CSV, TSV, MAT, NPY, H5AD, and TXT helper files and only escalates to generated code when the request truly needs calculations, transformations, or plots.

Operations:
- metadata: Extract dataset metadata (columns, types, samples)
- profile: Deterministic dataset profile for row/column counts, sample values, and simple ID overlap
- generate: Generate Python analysis code based on task description
- execute: Execute Python code via Claude Code
- analyze: Full pipeline (metadata → generate → execute with auto-fix)

For quick local inspection (overview, schema, columns, row/column counts, previews), prefer metadata/profile first and keep the path lightweight. Use analyze only when the user clearly needs code-backed analysis or visualization.

Params for analyze (recommended):
{"operation": "analyze", "file_paths": ["/path/to/data.csv"], "task_title": "Analysis Title", "task_description": "What to analyze"}

Params for metadata:
{"operation": "metadata", "file_path": "/path/to/data.csv"}

Params for profile:
{"operation": "profile", "file_paths": ["/path/to/data.csv", "/path/to/ids.txt"]}

Use this for data exploration, statistical analysis, and visualization tasks on structured data files.""",
            "plan_operation": """Plan creation and optimization tool for structured task planning.

Operations:
- create: Create a new plan with tasks. Params: {"operation": "create", "title": "Plan Title", "description": "Goal", "tasks": [{"name": "Task 1", "instruction": "Details...", "dependencies": ["Task 0"]}]}
- review: Review plan quality and structure. Returns BOTH: (1) structural health_score and (2) a strict research-plan rubric_score with detailed breakdown. Params: {"operation": "review", "plan_id": 123}
- optimize: Apply changes to improve the plan. Params: {"operation": "optimize", "plan_id": 123, "changes": [{"action": "add_task|update_task|update_description|delete_task|reorder_task", ...}]}
- get: Get plan details. Params: {"operation": "get", "plan_id": 123}

WORKFLOW for Plan Creation:
1. If the request depends on latest external evidence or best practices, optionally use web_search first
2. For a new plan, call 'create' directly from the current context
3. After a successful new-plan create, prefer 'review' so rubric metadata is available for the UI and later optimization
4. For a bound plan, use 'get', 'review', or 'optimize' on the existing plan_id
5. Use 'optimize' only when review or user feedback gives you concrete changes to apply
6. Report the real plan result to the user with plan_id and rubric status when available

IMPORTANT:
- When creating plans, ensure each task has clear, actionable instructions.
- For optimize/update_task, put editable fields at the top level (name/instruction/dependencies). Do not send only nested updated_fields values.
- If the user asks to update the plan description or rationale summary, use action='update_description' with a top-level description field.
- Do NOT use this tool to mark the currently executing task completed/failed. Current task status is auto-synced from tool execution; use this tool only for structural plan changes.""",
            "terminal_session": """Interactive terminal (PTY shell) for running commands directly.

Operations:
- write: Send command and get output. Params: {"operation": "write", "data": "pwd\\n"}. terminal_id is auto-resolved — no need to call ensure first. Returns {output, status, verification_state, exit_code, ...}. `success` means bytes reached the PTY. `status` is "completed" only when output briefly idles (PTY settle), NOT guaranteed shell success. For local mutations, trust `verification_state`: verified_success / verified_failure / unverified — never infer completion from status="completed" alone.
- replay: Get recent terminal output. Params: {"operation": "replay", "terminal_id": "tid", "limit": 50}
- list: List active terminal sessions. Params: {"operation": "list"}
- close: Close a terminal session. Params: {"operation": "close", "terminal_id": "tid"}
- ensure: Explicitly get or create a terminal. Params: {"operation": "ensure", "session_id": "chat_session_id"}. Returns terminal_id.

Typical usage: just call write with data — the system handles terminal creation automatically.
IMPORTANT: data must end with \\n to execute the command.""",
            "manuscript_writer": (
                "PREFERRED tool for writing research papers and manuscripts. "
                "Generates publication-quality LaTeX/Markdown sections with proper citations. "
                "Params: {\"task\": \"write the introduction section\", \"output_path\": \"/abs/path/output.md\", "
                "\"context_paths\": [\"/path/to/refs.bib\", \"/path/to/data.csv\"], "
                "\"analysis_path\": \"/path/to/analysis_results\"}. "
                "IMPORTANT: For ANY paper/manuscript writing task (sections, drafts, revisions, assembly), "
                "ALWAYS use manuscript_writer instead of code_executor. "
                "code_executor should NEVER be used to write paper content directly."
            ),
            "literature_pipeline": (
                "Collect a literature evidence pack from PubMed/PMC. "
                "Returns references.bib, evidence.md, and library.jsonl for downstream use. "
                "Params: {\"query\": \"pseudomonas phage\", optional \"max_results\", \"download_pdfs\", \"session_id\"}."
            ),
            "review_pack_writer": (
                "Generate a literature-backed review draft by chaining literature_pipeline "
                "and manuscript_writer. "
                "Params: {\"topic\": \"Pseudomonas phage\", optional \"query\", \"max_results\", "
                "\"download_pdfs\", \"sections\", \"max_revisions\", \"evaluation_threshold\", \"session_id\"}."
            ),
            "deliverable_submit": (
                "Promote specific files into the session Deliverables bundle. "
                "Params: {\"publish\": true|false, \"artifacts\": [{\"path\": \"/path/to/file\", "
                "\"module\": \"code|image_tabular|paper|refs|docs\", optional \"reason\": \"note\"}]}. "
                "Use this after files already exist and the user wants them included in Deliverables."
            ),
        }

        tools_desc = []
        for t in self.available_tools:
            if t in tool_descriptions:
                tools_desc.append(f"- {t}: {tool_descriptions[t]}")
            else:
                tools_desc.append(f"- {t}")
        tools_text = "\n".join(tools_desc)

        if task_context and task_context.task_instruction:
            task_lines = [
                "You are a task execution engine in DeepThink mode.",
                "Focus on completing the specific task with verifiable outputs.",
                "Be concise, deterministic, and robust.",
                "",
                "=== TASK EXECUTION CONTEXT ===",
            ]
            if task_context.task_id is not None:
                task_lines.append(f"Task ID: {task_context.task_id}")
            if task_context.task_name:
                task_lines.append(f"Task Name: {task_context.task_name}")
            task_lines.append(f"Instruction: {task_context.task_instruction}")
            if task_context.constraints:
                task_lines.append("Constraints:")
                for c in task_context.constraints:
                    task_lines.append(f"- {c}")
            if task_context.plan_outline:
                task_lines.append("Plan Outline (truncated):")
                task_lines.append(task_context.plan_outline)
            if task_context.dependency_outputs:
                task_lines.append("Dependency Outputs:")
                for dep in task_context.dependency_outputs[:6]:
                    task_lines.append(f"- {json.dumps(dep, ensure_ascii=False)[:600]}")
            if task_context.context_summary:
                task_lines.append("Task Reference Summary:")
                task_lines.append(self._clip_reference_text(task_context.context_summary, limit=1500))
            if task_context.context_sections:
                task_lines.append("Task Reference Sections:")
                for section in task_context.context_sections[:6]:
                    if not isinstance(section, dict):
                        continue
                    title = self._clip_reference_text(section.get("title") or "Section", limit=120)
                    content = self._clip_reference_text(section.get("content"), limit=700)
                    task_lines.append(f"- {title}: {content}")
            if task_context.paper_context_paths:
                task_lines.append("Paper Context Paths:")
                for path in task_context.paper_context_paths[:10]:
                    task_lines.append(f"- {path}")
            if task_context.skill_context:
                task_lines.append("")
                task_lines.append("=== SKILL GUIDANCE ===")
                task_lines.append(task_context.skill_context)
            task_lines.append("")
            base_prompt = "\n".join(task_lines) + "\n"
        else:
            base_prompt = ""

        base_prompt += f"""You are a Deep Thinking AI Assistant.
Your goal is to choose the right depth for the user's request: be thorough when needed, but do not over-research simple questions.

{self._build_shared_strategy_block()}
{self._build_request_tier_block()}
{self._build_structured_plan_requirement_block()}
{self._build_capability_floor_block()}
{self._build_grounded_tooling_block()}
=== THINKING WORKFLOW ===
1. First classify whether the request needs tools, targeted evidence, or just a direct answer.
2. Break the query into sub-problems only when that materially helps.
3. Use tools selectively and synthesize only the evidence needed for the user's request.
4. Provide final_answer once the request is adequately answered.

=== AVAILABLE TOOLS ===
{tools_text}

=== BIO_TOOLS OPERATING RULES ===
- For accession-based FASTA download, call sequence_fetch first and use its output_file for downstream steps.
- For FASTA/FASTQ/sequence tasks, start with bio_tools (typically seqkit stats for first-pass diagnostics).
- Use operation="help" before first use of uncertain operations; do not guess parameters.
- If routing remains uncertain after help, use focused web_search and retry bio_tools.
- Keep quick checks synchronous; use background=true only for clearly long-running jobs and return job_id for job_status follow-ups.
- Bio_tools catalog (synced from tools_config.json): {_BIO_TOOLS_CATALOG_TEXT}

=== BIO_TOOLS RECOVERY PROTOCOL (MANDATORY) ===
1. Call bio_tools(..., operation="help") and inspect required parameters.
2. Retry bio_tools with corrected parameters and verified absolute paths.
3. If still failing, run targeted web_search for operation/parameter mapping.
4. If still failing for reasons other than input parsing/conversion, use code_executor for minimal shell-level diagnostics.
Try at least 3 different recovery attempts before reporting failure.

=== PLAN CREATION RULE ===
Research before planning only when current external best practices or factual verification materially affect the plan. Otherwise create or update the structured plan directly from the current context first.

{self._build_protocol_boundary_block("legacy")}
=== OUTPUT FORMAT ===
Respond with valid JSON only (no markdown fences):

{{
  "thinking": "What you are analyzing and why...",
  "action": {{"tool": "tool_name", "params": {{...}}}},
  "final_answer": null
}}

When ready to answer:
{{
  "thinking": "Synthesis based on tool evidence...",
  "action": null,
  "final_answer": {{"answer": "Comprehensive answer here", "confidence": 0.9}}
}}

=== RULES ===
1. Output valid JSON only.
2. Use action to call one tool per response.
3. Do NOT call tools for greetings, casual follow-ups, simple explanations, or opinion-style questions unless the user explicitly asks for research or sources.
4. Call MULTIPLE tools only when evidence gathering is genuinely required.
5. Include key tool evidence before concluding when evidence was used.
6. For PhageScope submit, prioritize non-blocking backend execution over immediate result fetching.
"""
        return self._append_reference_context(base_prompt, context)

    def _get_next_step_prompt(self, iteration: int) -> str:
        """Generate prompt for the next step, encouraging completion if steps are getting long."""
        if self._requires_structured_plan():
            if iteration >= self.max_iterations - 1:
                return (
                    "CRITICAL: This request requires a real structured plan action. "
                    "If no successful plan_operation has happened yet, call it NOW. "
                    "Only use submit_final_answer after a real create/update succeeds, or to clearly report that the structured plan was not created or updated."
                )
            return self._get_structured_plan_retry_prompt()
        tier = self._request_tier()
        if tier == "light":
            return (
                "Light request: if you still need file or tool evidence to answer "
                "accurately, call the tool now; otherwise finish with submit_final_answer."
            )
        if tier == "standard":
            return 'Prefer answering now. Continue only if one more brief step materially improves the response.'
        if iteration >= self.max_iterations - 1:
            return (
                'CRITICAL: This is your LAST step. You MUST call submit_final_answer NOW with the best answer '
                'you can provide based on all evidence gathered. Do NOT call any more tools — synthesize and submit.'
            )
        if iteration > 8:
            return (
                'You have taken many steps. Consolidate what you already know and call submit_final_answer NOW. '
                'Do NOT continue researching unless one more targeted tool call is absolutely essential.'
            )
        elif iteration > 5:
            return (
                'Check whether the user is already adequately answered. If yes, call submit_final_answer now. '
                'Continue only if another step materially improves accuracy.'
            )
        else:
            return 'Before continuing, ask whether another step or tool call is truly needed. If the current information is enough, call submit_final_answer now.'

    def _parse_llm_response_safe(self, response: str) -> tuple[Dict[str, Any], Optional[str]]:
        try:
            return self._parse_llm_response(response), None
        except Exception as exc:
            return {}, str(exc)

    def _parse_llm_response(self, response: str) -> Dict[str, Any]:
        json_str = self._extract_json(response)
        parsed: Optional[Dict[str, Any]] = None
        parse_errors: List[str] = []

        for candidate in (
            json_str,
            self._repair_json_text(json_str),
        ):
            if not candidate:
                continue
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError as exc:
                parse_errors.append(str(exc))
                continue
            if isinstance(payload, dict):
                parsed = payload
                break

        if parsed is None:
            parsed = self._regex_parse_fallback(json_str)
            if parsed is None:
                raise DeepThinkProtocolError(
                    "LLM output is not valid JSON; parse errors: "
                    + "; ".join(parse_errors[:2])
                )

        thinking = parsed.get("thinking", "")
        if thinking is None:
            thinking = ""
        if not isinstance(thinking, str):
            thinking = str(thinking)

        result = {
            "thought": thinking,
            "is_final": False,
            "final_answer": "",
            "confidence": 0.0,
            "tool_name": None,
            "tool_params": None,
            "action_str": None,
        }

        final_ans = parsed.get("final_answer")
        if final_ans is not None:
            if isinstance(final_ans, str):
                final_ans = {"answer": final_ans, "confidence": 0.7}
            if not isinstance(final_ans, dict):
                raise DeepThinkProtocolError("final_answer must be an object or null.")
            answer = final_ans.get("answer")
            if not isinstance(answer, str) or not answer.strip():
                raise DeepThinkProtocolError("final_answer.answer must be a non-empty string.")
            confidence_raw = final_ans.get("confidence", 0.8)
            try:
                confidence = float(confidence_raw)
            except (TypeError, ValueError):
                confidence = 0.8
            result["is_final"] = True
            result["final_answer"] = answer
            result["confidence"] = min(max(confidence, 0.0), 1.0)
            return result

        action = parsed.get("action")
        if action is not None:
            if isinstance(action, str):
                action = {"tool": action, "params": {}}
            if not isinstance(action, dict):
                raise DeepThinkProtocolError("action must be an object or null.")
            tool_name = action.get("tool")
            if not isinstance(tool_name, str) or not tool_name.strip():
                raise DeepThinkProtocolError("action.tool must be a non-empty string.")
            tool_params = action.get("params", {})
            if tool_params is None:
                tool_params = {}
            if not isinstance(tool_params, dict):
                tool_params = {}
            result["tool_name"] = tool_name.strip()
            result["tool_params"] = tool_params
            result["action_str"] = json.dumps(
                {"tool": result["tool_name"], "params": tool_params},
                ensure_ascii=False,
            )

        return result

    def _repair_json_text(self, text: str) -> str:
        repaired = (text or "").strip()
        if not repaired:
            return repaired
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
        repaired = repaired.replace("None", "null")
        if "'" in repaired and '"' not in repaired:
            repaired = repaired.replace("'", '"')
        return repaired

    def _regex_parse_fallback(self, text: str) -> Optional[Dict[str, Any]]:
        body = (text or "").strip()
        if not body:
            return None
        thinking_match = re.search(r'"thinking"\s*:\s*"([^"]*)"', body, re.DOTALL)
        final_match = re.search(r'"answer"\s*:\s*"([^"]+)"', body, re.DOTALL)
        tool_match = re.search(r'"tool"\s*:\s*"([^"]+)"', body)

        payload: Dict[str, Any] = {}
        if thinking_match:
            payload["thinking"] = thinking_match.group(1)
        if final_match:
            payload["final_answer"] = {"answer": final_match.group(1), "confidence": 0.6}
        if tool_match:
            payload["action"] = {"tool": tool_match.group(1), "params": {}}
        return payload or None

    @staticmethod
    def _try_parse_structured_actions(content: str) -> List[Dict[str, Any]]:
        """Try to parse LLMStructuredResponse-style JSON actions from text.

        Returns a list of action dicts [{name, parameters}] or empty list
        if the content is not a valid structured response.
        """
        text = (content or "").strip()
        if not text or not text.startswith("{"):
            return []
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end <= start:
                return []
            try:
                payload = json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return []

        if not isinstance(payload, dict):
            return []
        actions_raw = payload.get("actions")
        if not isinstance(actions_raw, list) or not actions_raw:
            return []

        result: List[Dict[str, Any]] = []
        for action in actions_raw:
            if not isinstance(action, dict):
                continue
            name = action.get("name") or ""
            if not name:
                continue
            result.append({
                "name": str(name),
                "parameters": action.get("parameters") or {},
                "kind": action.get("kind", "tool_operation"),
            })
        return result

    def _extract_json(self, text: str) -> str:
        """Extract the first complete top-level JSON object."""
        text = (text or "").strip()
        if not text:
            raise DeepThinkProtocolError("LLM output is empty.")

        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].lstrip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        brace_count = 0
        start_idx = -1
        for i, char in enumerate(text):
            if char == '{':
                if brace_count == 0:
                    start_idx = i
                brace_count += 1
            elif char == '}':
                if brace_count == 0:
                    continue
                brace_count -= 1
                if brace_count == 0 and start_idx >= 0:
                    return text[start_idx:i+1]

        raise DeepThinkProtocolError("No complete JSON object found in LLM output.")

    @staticmethod
    def _tool_names_from_payload(payload: Any) -> List[str]:
        names: List[str] = []
        if not isinstance(payload, dict):
            return names
        single = payload.get("tool")
        if isinstance(single, str) and single.strip():
            names.append(single.strip())
        tools = payload.get("tools")
        if isinstance(tools, list):
            for item in tools:
                if isinstance(item, dict):
                    t = item.get("tool")
                    if isinstance(t, str) and t.strip():
                        names.append(t.strip())
        return names

    @classmethod
    def _collect_tool_usage_counts(cls, steps: List[ThinkingStep]) -> Dict[str, int]:
        c: Counter[str] = Counter()
        for step in steps:
            if not step.action:
                continue
            try:
                payload = json.loads(step.action)
            except Exception:
                continue
            for name in cls._tool_names_from_payload(payload):
                if name and name != "submit_final_answer":
                    c[name] += 1
        return dict(c)

    @staticmethod
    def _format_tool_usage_counts(counts: Dict[str, int]) -> str:
        if not counts:
            return ""
        items = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
        return ", ".join(f"{k}×{v}" for k, v in items)

    @staticmethod
    def _select_steps_for_summary(steps: List[ThinkingStep]) -> List[ThinkingStep]:
        n = len(steps)
        if n <= 5:
            return list(steps)
        idx = sorted({0, 1, n - 3, n - 2, n - 1})
        idx = [i for i in idx if 0 <= i < n]
        return [steps[i] for i in idx]

    @staticmethod
    def _slim_evidence_text_for_synthesis(text: str) -> str:
        """Drop internal tool-output paths and compress terminal_session JSON for synthesis.

        Important: tool results are often a *single* JSON line containing ``storage`` paths.
        Line-based removal previously deleted the entire line, leaving **empty evidence** for
        fallback synthesis — causing the LLM to hallucinate \"no tool output\" answers.
        """
        stripped = str(text or "").strip()
        if not stripped:
            return ""
        candidate = _TOOL_RESULT_PREFIX_RE.sub("", stripped).strip()

        def _drop_storage_keys(node: Any) -> Any:
            if isinstance(node, dict):
                return {
                    k: _drop_storage_keys(v)
                    for k, v in node.items()
                    if k not in {"storage", "deliverables"}
                }
            if isinstance(node, list):
                return [_drop_storage_keys(x) for x in node]
            return node

        try:
            obj = json.loads(candidate)
        except Exception:
            # Non-JSON: avoid deleting one-line blobs; redact path substrings only.
            if "/tool_outputs/" in stripped.lower():
                redacted = re.sub(
                    r"/[^\s\"']*tool_outputs[^\s\"']*",
                    "[internal_tool_output_path]",
                    stripped,
                    flags=re.IGNORECASE,
                )
                return redacted[:12000]
            return stripped[:12000]

        if isinstance(obj, dict):
            obj = _drop_storage_keys(obj)
            if str(obj.get("tool") or "") == "terminal_session" or "terminal_id" in obj:
                slim: Dict[str, Any] = {
                    "tool": "terminal_session",
                    "operation": obj.get("operation"),
                    "verification_state": obj.get("verification_state"),
                    "command_state": obj.get("command_state"),
                    "exit_code": obj.get("exit_code"),
                    "verification_summary": obj.get("verification_summary"),
                    "status": obj.get("status"),
                    "output": obj.get("output"),
                }
                if isinstance(obj.get("verification_evidence"), dict):
                    slim["verification_evidence"] = obj.get("verification_evidence")
                compact = {k: v for k, v in slim.items() if v is not None}
                out = json.dumps(compact, ensure_ascii=False)
                return out[:12000]
            return json.dumps(obj, ensure_ascii=False)[:12000]
        return str(obj)[:12000]

    def _collect_evidence_snippets(
        self,
        steps: List[ThinkingStep],
        *,
        max_steps: int = 8,
        max_chars: int = 3000,
        per_snippet_max: int = 900,
    ) -> str:
        parts: List[str] = []
        total = 0
        count = 0
        for step in reversed(steps):
            if count >= max_steps:
                break
            ar = step.action_result
            if not isinstance(ar, str) or not ar.strip():
                continue
            text = self._slim_evidence_text_for_synthesis(ar)
            text = " ".join(text.split()).strip()
            if len(text) > per_snippet_max:
                text = text[: per_snippet_max - 3] + "..."
            block = f"[Step {step.iteration}]\n{text}"
            if total + len(block) + 2 > max_chars:
                remaining = max(0, max_chars - total - 20)
                if remaining > 80:
                    parts.append(f"[Step {step.iteration}]\n{text[:remaining]}...")
                break
            parts.append(block)
            total += len(block) + 2
            count += 1
        parts.reverse()
        return "\n\n".join(parts)

    @staticmethod
    def _humanize_single_tool_result(tool_name: str, obj: dict) -> str:
        """Convert a single parsed tool-result dict into a concise, human-readable line."""
        success = obj.get("success")
        result = obj.get("result") or obj
        tool = str(result.get("tool", "") or tool_name).strip()

        # --- terminal_session: skip noise-only entries ---
        if tool == "terminal_session" or "terminal_id" in obj:
            output = str(result.get("output") or obj.get("output") or "").strip()
            vs = result.get("verification_summary") or obj.get("verification_summary")
            if vs:
                return f"终端会话：{vs}"
            if output and len(output) > 15:
                return f"终端输出：{output[:300]}"
            return ""  # skip noise

        # --- code_executor ---
        if tool == "code_executor":
            if success is True or result.get("success") is True:
                stdout = str(result.get("stdout") or "").strip()
                artifacts = result.get("artifact_paths") or []
                result_files = [
                    p for p in artifacts
                    if "/results/" in str(p) and not str(p).rstrip("/").endswith("/results")
                ]
                parts: List[str] = ["代码执行成功"]
                if stdout:
                    # Take the most informative lines (skip blank / separator lines)
                    lines = [ln.strip() for ln in stdout.splitlines() if ln.strip() and ln.strip("=- ")]
                    if lines:
                        preview = "; ".join(lines[:5])
                        if len(preview) > 300:
                            preview = preview[:297] + "..."
                        parts.append(f"输出：{preview}")
                if result_files:
                    names = [p.rsplit("/", 1)[-1] for p in result_files[:8]]
                    parts.append(f"产出文件：{', '.join(names)}")
                return "。".join(parts)
            else:
                error = str(result.get("error") or result.get("stderr") or "unknown error").strip()
                if len(error) > 200:
                    error = error[:197] + "..."
                return f"代码执行失败：{error}"

        # --- file_operations ---
        if tool == "file_operations":
            op = str(result.get("operation") or "").strip()
            if op == "list":
                items = result.get("items") or []
                file_names = [
                    f"{it.get('name', '?')} ({it.get('size', '?')} B)"
                    for it in items if isinstance(it, dict)
                ][:10]
                if file_names:
                    return f"文件列表：{', '.join(file_names)}"
                return f"文件列表：空（{result.get('path', '')}）"
            if op == "read":
                summary = str(result.get("summary") or "").strip()
                path = str(result.get("path") or "").strip()
                fname = path.rsplit("/", 1)[-1] if path else "?"
                if summary:
                    return f"文件读取 ({fname})：{summary[:200]}"
                return f"已读取文件：{fname}"
            if op == "write":
                path = str(result.get("path") or "").strip()
                fname = path.rsplit("/", 1)[-1] if path else "?"
                size = result.get("size", "?")
                return f"已写入文件：{fname} ({size} B)"
            return f"文件操作 ({op})：{result.get('summary', '完成')}"

        # --- generic tool with summary ---
        summary = str(result.get("summary") or "").strip()
        if summary and len(summary) > 10:
            if tool:
                return f"{tool}：{summary[:300]}"
            return summary[:300]
        if success is True:
            return f"{tool}：执行完成" if tool else "工具执行完成"
        if success is False:
            error = str(result.get("error") or "").strip()
            if tool:
                return f"{tool}：失败{f' — {error[:150]}' if error else ''}"
            return f"执行失败{f'：{error[:150]}' if error else ''}"
        return ""

    def _collect_user_facing_evidence_snippets(
        self,
        steps: List[ThinkingStep],
        *,
        max_steps: int = 8,
        max_chars: int = 3000,
        per_snippet_max: int = 900,
    ) -> str:
        """Build human-readable bullet list from raw step action_results.

        Unlike _collect_evidence_snippets (which preserves JSON for LLM synthesis),
        this method humanizes tool results into natural-language summaries for direct
        display to the user.
        """
        bullets: List[str] = []
        total = 0
        count = 0
        for step in reversed(steps):
            if count >= max_steps:
                break
            ar = step.action_result
            if not isinstance(ar, str) or not ar.strip():
                continue
            count += 1

            # action_result may contain multiple tool results:
            # "[tool_a] {json}\n\n[tool_b] {json}"
            segments = re.split(r"\n\n(?=\[)", ar)
            for seg in segments:
                seg = seg.strip()
                if not seg:
                    continue
                # Extract tool name prefix if present: [tool_name] rest
                prefix_match = re.match(r"^\[([^\]]+)]\s*", seg)
                tool_name = prefix_match.group(1) if prefix_match else ""
                body = seg[prefix_match.end():] if prefix_match else seg

                # Try to parse as JSON and humanize
                humanized = None  # None = not parsed; "" = parsed but skip
                try:
                    obj = json.loads(body)
                    if isinstance(obj, dict):
                        humanized = self._humanize_single_tool_result(tool_name, obj)
                except Exception:
                    pass

                # humanized == "" means the humanizer explicitly says "skip this"
                if humanized == "":
                    continue
                if humanized is None:
                    # Non-JSON or unrecognized: use a trimmed version
                    cleaned = " ".join(body.split()).strip()
                    if len(cleaned) > per_snippet_max:
                        cleaned = cleaned[: per_snippet_max - 3] + "..."
                    if cleaned and len(cleaned) > 15:
                        humanized = cleaned
                if not humanized:
                    continue
                if len(humanized) > per_snippet_max:
                    humanized = humanized[: per_snippet_max - 3] + "..."
                bullet = f"- {humanized}"
                if total + len(bullet) + 1 > max_chars:
                    break
                bullets.append(bullet)
                total += len(bullet) + 1

        bullets.reverse()
        return "\n".join(bullets)

    def _build_bound_execute_task_fallback(
        self,
        steps: List[ThinkingStep],
        *,
        user_query: str,
        task_context: Optional[TaskExecutionContext],
    ) -> str:
        if not (
            self._is_execute_task_request()
            and self._has_bound_task_context(task_context)
            and self._explicit_task_override_active(task_context)
        ):
            return ""
        language = detect_reasoning_language(user_query)
        task_id = self._current_bound_task_id(task_context)
        task_name = str(getattr(task_context, "task_name", "") or "").strip()
        task_label = f"Task {task_id}" if task_id is not None else "the current bound task"
        if task_name:
            task_label = f"{task_label} ({task_name})"
        counts = self._collect_tool_usage_counts(steps)
        stats = self._format_tool_usage_counts(counts)
        evidence = self._collect_evidence_snippets(
            steps,
            max_steps=8,
            max_chars=1800,
            per_snippet_max=500,
        ).strip()
        if language == "zh":
            header = f"本轮已经进入已绑定任务执行链{f'（{stats}）' if stats else ''}，但系统在结束前没有成功提交正式最终答案。"
            lines = [
                header,
                f"当前绑定任务：{task_label}。",
                "这不表示缺少任务定义；当前任务上下文已经存在，后续应从当前绑定任务继续执行，而不是再要求用户补任务描述。",
            ]
            if evidence:
                lines.extend(["", "已观察到的关键信息：", evidence])
            return "\n".join(lines).strip()
        header = (
            f"This run stayed inside a bound execute-task chain{f' ({stats})' if stats else ''}, "
            "but it ended before a formal final answer was submitted."
        )
        lines = [
            header,
            f"Current bound task: {task_label}.",
            "This does not mean the task definition is missing; the task context is already bound and execution should continue from the current task instead of asking the user to provide task details again.",
        ]
        if evidence:
            lines.extend(["", "Observed evidence:", evidence])
        return "\n".join(lines).strip()

    def _build_structured_fallback(self, steps: List[ThinkingStep], user_query: str = "") -> str:
        """Last-resort fallback: include evidence excerpts rather than a generic 'no answer' message."""
        language = detect_reasoning_language(user_query)

        # Collect any meaningful evidence to include in the fallback
        evidence = self._collect_user_facing_evidence_snippets(
            steps,
            max_steps=12,
            max_chars=4000,
            per_snippet_max=1200,
        )
        # Also collect useful thoughts
        useful_thoughts: List[str] = []
        for s in reversed(steps):
            if isinstance(s.thought, str) and s.thought.strip() and not is_process_only_answer(s.thought, user_query=user_query):
                cleaned = s.thought.strip()
                if len(cleaned) > 50:
                    useful_thoughts.append(cleaned[:800])
                    if len(useful_thoughts) >= 3:
                        break
        useful_thoughts.reverse()

        # If we have substantial evidence or thoughts, build a content-rich fallback
        if evidence.strip() and len(evidence.strip()) > 20:
            # Check if we have successful tool execution evidence
            has_success = "代码执行成功" in evidence or "已写入文件" in evidence or "产出文件" in evidence
            if language == "zh":
                if has_success:
                    header = "以下是本轮工具执行的结果摘要：\n\n"
                    footer = ""
                else:
                    header = "以下是本轮执行中观察到的信息：\n\n"
                    footer = "\n\n如需更详细的分析，请指出具体要查看的内容。"
            else:
                if has_success:
                    header = "Here is the summary of tool execution results:\n\n"
                    footer = ""
                else:
                    header = "Here is what was observed during execution:\n\n"
                    footer = "\n\nFor a more detailed analysis, please specify what you'd like to examine."
            return header + evidence.strip() + footer

        if useful_thoughts:
            combined = "\n\n".join(useful_thoughts)
            if language == "zh":
                return f"我先给出目前已经收敛出的关键判断：\n\n{combined}"
            return f"Here are the key conclusions that could still be supported:\n\n{combined}"

        # Truly nothing useful — keep minimal message
        if language == "zh":
            return (
                "这一轮检查还不足以支撑可靠结论。"
                "建议进一步缩小范围，或指定要继续核查的对象后再收敛。"
            )
        return (
            "The completed checks were not enough to support a reliable final answer. "
            "This request likely needs a narrower scope or a more specific fact to verify next."
        )

    async def _generate_fallback_from_evidence(
        self,
        user_query: str,
        evidence_snippets: str,
        steps: List[ThinkingStep],
        task_context: Optional[TaskExecutionContext] = None,
        *,
        max_retries: int = 3,
        timeout: float = 45,
        max_tokens: int = 2000,
    ) -> str:
        if not hasattr(self.llm_client, "chat_async"):
            raise DeepThinkProtocolError("LLM client does not support chat_async")
        n = len(steps)
        uq = (user_query or "").strip()

        # Collect useful thoughts as additional context
        thought_context = ""
        useful_thoughts = [
            s.thought.strip()
            for s in steps
            if isinstance(s.thought, str) and len(s.thought.strip()) > 30
        ]
        if useful_thoughts:
            thought_context = (
                "\n\nAssistant's reasoning during the process:\n"
                + "\n".join(f"- {t[:300]}" for t in useful_thoughts[-5:])
                + "\n"
            )

        focus_instruction = ""
        if self._is_brief_execute_followup():
            focus_instruction = (
                "7) This is a short execution follow-up. Focus on the current task outcome and latest successful tool result.\n"
                "8) Do NOT recap prior project milestones, historical progress tables, or next-step menus unless the user explicitly asked for them.\n"
            )
        bound_task_instruction = ""
        if self._is_execute_task_request() and self._has_bound_task_context(task_context):
            task_id = self._current_bound_task_id(task_context)
            task_name = str(getattr(task_context, "task_name", "") or "").strip()
            task_label = f"Task {task_id}" if task_id is not None else "the current bound task"
            if task_name:
                task_label = f"{task_label} ({task_name})"
            bound_task_instruction = (
                f"9) This request is already bound to {task_label}. "
                "Do NOT ask the user to provide task definitions, task descriptions, or to confirm whether a parent plan68_task directory exists.\n"
                "10) If execution stopped early, summarize the real bound-task state and the observed outputs instead of inventing a missing-task-definition blocker.\n"
            )

        prompt = (
            f"User question:\n{uq[:2000]}\n\n"
            f"Below are excerpts from tool outputs collected during {n} reasoning step(s). "
            f"The system did not produce a final answer automatically, so you must synthesize one now.\n\n"
            f"{evidence_snippets}"
            f"{thought_context}\n\n"
            "INSTRUCTIONS:\n"
            "Respond in the same language as the user question. Provide a COMPLETE, USEFUL answer:\n"
            "1) Directly answer the user's question based on the evidence available.\n"
            "2) Summarize the key facts, findings, and data points from the excerpts.\n"
            "3) If the evidence is insufficient for a complete answer, clearly state what was found and what remains unknown.\n"
            "4) Use clear structure (headings, bullet points) for readability.\n"
            "5) Do NOT say 'I could not find an answer' if there is ANY useful information — present what was found.\n"
            "6) Do not invent dates or claims absent from the excerpts.\n"
            f"{focus_instruction}"
            f"{bound_task_instruction}"
            f"{PROFESSIONAL_STYLE_INSTRUCTION}"
        )

        last_exc: Optional[Exception] = None
        for attempt in range(max_retries):
            try:
                raw = await asyncio.wait_for(
                    self.llm_client.chat_async(prompt=prompt, max_tokens=max_tokens),
                    timeout=timeout,
                )
                cleaned = sanitize_professional_response_text(str(raw or "").strip())
                if len(cleaned) < 20:
                    raise ValueError(f"fallback synthesis too short ({len(cleaned)} chars)")
                return cleaned
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "DeepThink fallback synthesis attempt %d/%d failed: %s",
                    attempt + 1,
                    max_retries,
                    str(exc)[:200],
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(1.0 * (attempt + 1))

        raise last_exc or ValueError("fallback synthesis failed after retries")

    async def _forced_synthesis_from_steps(
        self,
        steps: List[ThinkingStep],
        user_query: str,
        messages: List[Dict[str, Any]],
        task_context: Optional[TaskExecutionContext] = None,
    ) -> str:
        """Make one final LLM call with all context asking it to synthesize a complete answer.

        This is more powerful than _generate_fallback_from_evidence because it
        includes both evidence snippets and the assistant's reasoning thoughts,
        giving the LLM rich context to produce a quality answer.
        """
        if not hasattr(self.llm_client, "chat_async"):
            return ""

        try:
            evidence = self._collect_evidence_snippets(
                steps, max_steps=12, max_chars=6000, per_snippet_max=1500
            )
            uq = (user_query or "").strip()
            if not uq:
                return ""

            # Also collect assistant thoughts for additional context
            thought_lines: List[str] = []
            for s in steps:
                if isinstance(s.thought, str) and len(s.thought.strip()) > 30:
                    thought_lines.append(f"[Step {s.iteration} thought]: {s.thought.strip()[:500]}")
            thoughts_text = "\n".join(thought_lines[-8:]) if thought_lines else ""

            language = detect_reasoning_language(user_query)
            structured_plan_outcome = self._summarize_structured_plan_outcome(
                steps,
                user_query=user_query,
            )
            if language == "zh":
                instruction = (
                    "你是一个深度思考AI助手。用户提出了一个问题，系统已经执行了多个工具调用来收集信息。"
                    "现在请基于下面收集到的所有证据和推理过程，直接回答用户的问题。\n"
                    "要求：\n"
                    "- 提供完整、有用的回答\n"
                    "- 使用清晰的结构（标题、要点列表）\n"
                    "- 即使信息不完整，也请尽力基于已有证据给出最佳回答\n"
                    "- 不要说'无法回答'，请展示已找到的信息\n"
                    "- 不要编造证据中没有的信息\n"
                    "- 严禁：若「证据」里没有某文件的完整读取内容，却声称已读取该文件；严禁根据常识列举 "
                    "`.env` 里「通常会有」的 Qwen/OpenAI/GLM 等名称，除非这些字符串出现在证据原文中。\n"
                    "- 若推理里打算读 A 文件，但证据实际只有 B 文件，必须在答案中说明「未读到 A」或「仅见 B」，不得假装已检查 A。\n"
                    "- 若用户要验证 PhageScope 远程访问，而证据中没有任何 phagescope 工具的成功返回，"
                    "必须明确写出「尚未执行 phagescope 远程请求」，不得声称已确认 PhageScope 权限或「优化已验证 PhageScope」。\n"
                    "- 介绍 PhageScope 接口时不要臆造「必须去官网用户中心申请 API Token」等；官方文档以 `userid` 为主，"
                    "工具里 `token` 为可选；无证据时不要断言具体凭证流程。\n"
                    "- 不要引导用户去查看、粘贴 `.env` 来「验证 PhageScope」；除非用户明确问本地部署密钥。"
                    "连通性应以 `phagescope` 工具（如 ping）为准；勿臆造 `PHAGESCOPE_API_TOKEN` 等变量名。"
                )
                if structured_plan_outcome.get("required") and not structured_plan_outcome.get("satisfied"):
                    instruction += (
                        "\n- 这次请求要求产出真实的结构化计划。当前证据里没有成功的 `plan_operation` 创建/更新结果。"
                        "\n- 你必须明确写出：本轮未成功创建或更新结构化计划；不要把普通 markdown 文本说成已经创建好的计划。"
                    )
                if self._is_brief_execute_followup():
                    instruction += (
                        "\n- 这是一次简短的执行跟进。优先汇报当前任务结果或最新工具结果。"
                        "\n- 不要回顾先前项目里程碑、旧测试轮次、阶段性总结或“下一步建议”，除非用户明确要求。"
                    )
            else:
                instruction = (
                    "You are a Deep Thinking AI assistant. The user asked a question and the system "
                    "has executed multiple tool calls to gather information. Now synthesize ALL the "
                    "evidence and reasoning below into a complete, useful answer.\n"
                    "Requirements:\n"
                    "- Provide a complete, useful answer\n"
                    "- Use clear structure (headings, bullet points)\n"
                    "- Even if information is incomplete, provide the best answer from available evidence\n"
                    "- Do NOT say you cannot answer — present what was found\n"
                    "- Do not invent claims absent from the evidence\n"
                    "- Do not claim a file was read unless its contents (or an explicit excerpt) appear in the evidence; "
                    "if reasoning mentions file A but evidence only shows file B, say so.\n"
                    "- Do not list typical `.env` API provider names unless they appear verbatim in the evidence.\n"
                    "- For PhageScope access checks: if no phagescope tool success appears in the evidence, "
                    "state clearly that remote PhageScope was not exercised; do not claim credentials or that ping succeeded.\n"
                    "- When describing PhageScope API requirements, do not invent a mandatory API token from a "
                    "\"user center\" unless the evidence says so; documented flows center on `userid`; tool `token` is optional.\n"
                    "- Do not tell the user to inspect or paste `.env` for PhageScope verification unless they explicitly "
                    "ask about local secrets; use phagescope tool (e.g. ping). This codebase only documents optional env "
                    "`PHAGESCOPE_BASE_URL` and `PHAGESCOPE_SSL_VERIFY`, not a `PHAGESCOPE_API_TOKEN` variable."
                )
                if structured_plan_outcome.get("required") and not structured_plan_outcome.get("satisfied"):
                    instruction += (
                        "\n- This request required a real structured plan result."
                        "\n- No successful plan_operation create/update is present in the evidence, so you must explicitly say that a structured plan was not created or updated in this run."
                        "\n- Do not present ordinary markdown text as if the system already created the plan."
                    )
                if self._is_brief_execute_followup():
                    instruction += (
                        "\n- This is a short execution follow-up. Prioritize the current task outcome or latest tool result."
                        "\n- Do not recap prior project milestones, older test rounds, progress summaries, or next-step menus unless the user explicitly asked."
                    )

            prompt = (
                f"{instruction}\n\n"
                f"User question: {uq[:2000]}\n\n"
            )
            if self._is_execute_task_request() and self._has_bound_task_context(task_context):
                task_id = self._current_bound_task_id(task_context)
                task_name = str(getattr(task_context, "task_name", "") or "").strip()
                task_instruction = str(getattr(task_context, "task_instruction", "") or "").strip()
                task_label = f"Task {task_id}" if task_id is not None else "the current bound task"
                if task_name:
                    task_label = f"{task_label} ({task_name})"
                prompt += (
                    "=== BOUND TASK CONTEXT ===\n"
                    f"Current bound task: {task_label}\n"
                    f"Task instruction: {task_instruction[:600]}\n"
                    "This task context is already authoritative. Do NOT ask the user to provide task definitions, task descriptions, or to confirm whether a parent plan68_task directory exists.\n\n"
                )
            prompt += f"=== EVIDENCE FROM TOOLS ===\n{evidence}\n\n"
            if thoughts_text:
                prompt += f"=== REASONING PROCESS ===\n{thoughts_text}\n\n"
            prompt += "Please provide your complete answer now:"

            raw = await asyncio.wait_for(
                self.llm_client.chat_async(prompt=prompt, max_tokens=3000),
                timeout=60,
            )
            cleaned = sanitize_professional_response_text(str(raw or "").strip())
            if len(cleaned) < 30 or is_process_only_answer(cleaned, user_query=user_query):
                logger.warning("[DEEP_THINK_NATIVE] Forced synthesis produced insufficient content (%d chars)", len(cleaned))
                return ""
            logger.info("[DEEP_THINK_NATIVE] Forced synthesis succeeded (%d chars)", len(cleaned))
            return cleaned
        except Exception:
            logger.warning("[DEEP_THINK_NATIVE] Forced synthesis failed", exc_info=True)
            return ""

    async def _fallback_answer_from_steps(
        self,
        steps: List[ThinkingStep],
        user_query: str = "",
        task_context: Optional[TaskExecutionContext] = None,
    ) -> str:
        language = detect_reasoning_language(user_query)
        if not steps:
            return _localized_text(
                language,
                "已完成思考，但暂未形成结构化结论。",
                "DeepThink finished without a structured final answer.",
            )
        evidence = self._collect_evidence_snippets(steps)
        uq = (user_query or "").strip()
        if self._is_research_or_execute() and evidence.strip() and uq:
            try:
                fallback_kwargs: Dict[str, Any] = {}
                if task_context is not None:
                    fallback_kwargs["task_context"] = task_context
                generated = await self._generate_fallback_from_evidence(
                    uq,
                    evidence,
                    steps,
                    **fallback_kwargs,
                )
                if generated and not self._should_reject_missing_task_definition_answer(
                    generated,
                    task_context=task_context,
                ):
                    return generated
            except Exception:
                logger.warning(
                    "DeepThink fallback synthesis from tool evidence failed; using structured fallback.",
                    exc_info=True,
                )
        useful = [
            s.thought
            for s in steps
            if isinstance(s.thought, str)
            and s.thought.strip()
            and not is_process_only_answer(s.thought, user_query=user_query)
        ]
        if useful and not self._is_research_or_execute():
            return sanitize_professional_response_text(
                sanitize_reasoning_text(
                    useful[-1].strip(),
                    language=language,
                    max_chars=180,
                )
                or useful[-1].strip()
            )
        bound_execute_fallback = self._build_bound_execute_task_fallback(
            steps,
            user_query=user_query,
            task_context=task_context,
        )
        if bound_execute_fallback:
            return bound_execute_fallback
        return self._build_structured_fallback(steps, user_query)

    async def _generate_summary(self, steps: List[ThinkingStep], user_query: str) -> str:
        """Build a concise DeepThink summary from visible steps (no LLM call).

        Previous implementation made an extra LLM API call here, which added
        1-10 seconds of latency *after* the final answer was already streamed
        to the user, delaying the ``final`` SSE event and keeping the UI in a
        "still thinking" state.  The frontend ``getProcessSummary`` already has
        a fallback that builds the summary from step display texts, so an LLM
        call is unnecessary.
        """
        language = detect_reasoning_language(user_query)
        selected = self._select_steps_for_summary(steps)
        labels: List[str] = []
        for step in selected:
            visible = build_user_visible_step(step, language=language)
            display_text = str(visible.get("display_text") or "").strip()
            if display_text:
                labels.append(display_text)
        if len(labels) > 1:
            return " → ".join(labels[:3])
        if labels:
            return labels[0]
        return _default_deepthink_summary(user_query)
