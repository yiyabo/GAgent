import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from app.services.execution.tool_executor import UnifiedToolExecutor
from app.services.foundation.settings import get_settings
from app.services.response_style import sanitize_professional_response_text
from app.services.tool_schemas import build_tool_schemas
from app.llm import update_usage_context
from app.services.deep_think import dispatch as _dispatch
from app.services.deep_think import gating as _gating
from app.services.deep_think import guards as _guards
from app.services.deep_think import prompts as _prompts
from app.services.deep_think import protocol as _protocol
from app.services.deep_think import synthesis as _synthesis
from app.services.deep_think.gating import (
    _answer_acknowledges_failed_status_counts,
    _looks_like_completion_claim_text,
    _looks_like_global_success_claim_text,
)
from app.services.deep_think.models import (
    DeepThinkProtocolError,
    DeepThinkResult,
    TaskExecutionContext,
    ThinkingStep,
)
from app.services.deep_think.text_utils import (
    _BARE_READ_MARKER_RE,
    _CLI_PROTOCOL_MARKERS,
    _DELIVERABLE_FILE_RE,
    _EXPECT_DATA_RE,
    _EXPECT_DOC_RE,
    _EXPECT_IMAGE_RE,
    _EXPECT_KIND_EXTS,
    _EXPECT_KIND_LABEL,
    _GUARD_DELIVERABLE_EXT_RE,
    _GUARD_DIGIT_RE,
    _GUARD_PATH_NORMALIZE_RE,
    _GUARD_PRODUCTIVE_DIR_RE,
    _GUARD_SCRATCH_RE,
    _INLINE_IMAGE_EXT_RE,
    _OUTPUT_FILE_RE,
    _PRODUCTIVE_SEGMENT_RE,
    _collect_deliverable_display_names,
    _collect_deliverable_file_names,
    _collect_output_file_names,
    _default_fallback_timeout_seconds,
    _default_max_consecutive_llm_failures,
    _default_synthesis_max_tokens,
    _default_synthesis_timeout_seconds,
    _derive_expected_outputs,
    _drop_process_echo_bullets,
    _ensure_inline_images,
    _failure_signature_break_count,
    _failure_signature_warn_count,
    _guard_json_payload,
    _looks_like_cli_protocol_json,
    _missing_expectations,
    _progress_free_break_streak,
    _progress_free_nudge_streak,
    _strip_cli_noise_from_multiline,
    _strip_cli_stream_noise,
    _time_budget_break_seconds,
    _time_budget_nudge_seconds,
)

logger = logging.getLogger(__name__)

_CJK_CHAR_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_INTERNAL_REASONING_RE = re.compile(
    r"(?:^|\b)(?:the user is asking me|i notice this is just|i should\b|i need to\b|i'm ready to help|continue thinking|thinking about next step)",
    re.IGNORECASE,
)


def _describe_exception(exc: Exception) -> str:
    exc_type = type(exc).__name__
    message = str(exc).strip()
    if message:
        return f"{exc_type}: {message}"
    return exc_type


def _classify_llm_provider_error(exc: Exception) -> Any:
    """Classify an LLM client exception with the shared provider classifier.

    Returns None when the classifier is unavailable or the failure is
    unknown/transient (treated as retryable by the circuit breaker).
    """
    try:
        from app.services.llm.llm_service import _classify_llm_exception
    except Exception:
        return None
    try:
        return _classify_llm_exception(exc)
    except Exception:
        return None


def _build_llm_unavailable_final_answer(classified: Any) -> str:
    """User-facing message when the run is aborted due to LLM provider failure."""
    error_code = str(getattr(classified, "error_code", "") or "")
    status = getattr(classified, "status_code", None)
    if error_code == "llm_insufficient_quota":
        return (
            "LLM 模型服务额度不足，本次任务已暂停。"
            "请联系管理员充值或调整模型配置后重试。"
        )
    if status == 401:
        return (
            "LLM 模型服务认证失败，本次任务已暂停。"
            "请联系管理员检查 API Key 配置后重试。"
        )
    if status == 403:
        return (
            "LLM 模型服务拒绝请求（HTTP 403，通常为额度不足或权限受限），本次任务已暂停。"
            "请联系管理员充值或检查配置后重试。"
        )
    if isinstance(status, int) and 400 <= status < 500:
        return (
            f"LLM 模型服务拒绝了请求（HTTP {status}），本次任务已暂停。"
            "请联系管理员检查模型服务配置后重试。"
        )
    return (
        "LLM 模型服务连续调用失败，本次任务已暂停。"
        "请稍后重试；若问题持续，请联系管理员检查模型服务状态。"
    )


# Native tool steps often prefix JSON: "[file_operations] {...}"
_EXPLORATORY_FILE_OPERATIONS = {"read", "list", "profile", "census", "exists", "info"}


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
    if tool_name in {"bio_tools", "phagescope", "deeppl", "sequence_fetch", "url_fetch"}:
        return _localized_text(language, "运行分析工具", "Running analysis tools")
    if tool_name == "vision_reader":
        return _localized_text(language, "分析图像内容", "Analyzing visual content")
    if tool_name == "lightrag_query":
        return _localized_text(language, "查询 LightRAG 知识库", "Querying LightRAG knowledge base")
    if tool_name == "graph_rag":
        return _localized_text(language, "查询本地小图谱", "Querying local triples graph")
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


class DeepThinkAgent:
    """
    Agent that performs multi-step reasoning and tool calling before answering.
    Supports streaming output for real-time display of thinking process.
    """

    DEFAULT_TOOL_TIMEOUT = 60
    FINAL_STREAM_CHUNK_CHARS = 30
    FINAL_STREAM_DELAY_SEC = 0.05  # 50 ms — lets the network flush each chunk separately
    MAX_IDENTICAL_TOOL_CALL_CYCLES = 12
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

    # Matches transitional narration that the LLM may emit as a standalone
    # response (e.g. "Now let me also search..."). Such content must NOT be
    # treated as a final answer under the standard-tier early-stop rule.
    _PROCESS_NARRATION_RE = re.compile(
        r"^(?:now\s+)?(?:let me|let's|i'll|i will|i'm going to|now i|i should|"
        r"接下来|让我|我先|我将|下面让我|我会先|我应该先).+",
        re.IGNORECASE,
    )

    def __init__(
        self,
        llm_client: Any,
        available_tools: List[str],
        tool_executor: Callable[[str, Dict[str, Any]], Any],
        max_iterations: int = 30,
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
        self._pause_event: Optional[asyncio.Event] = None
        self._pause_initially_set = True
        self._skip_current_step = False

    def _get_pause_event(self) -> asyncio.Event:
        """Lazily create pause event in the current event loop."""
        if self._pause_event is None:
            self._pause_event = asyncio.Event()
            if self._pause_initially_set:
                self._pause_event.set()
        return self._pause_event

    def pause(self) -> None:
        if self._pause_event is not None:
            self._pause_event.clear()
        self._pause_initially_set = False

    def resume(self) -> None:
        if self._pause_event is not None:
            self._pause_event.set()
        self._pause_initially_set = True

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

        return ordered

    def _request_tier(self) -> str:
        return str(self.request_profile.get("request_tier") or "").strip().lower()

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
        brevity_hint = bool(self.request_profile.get("brevity_hint"))
        return tier == "execute" and brevity_hint

    def _is_execute_task_request(self) -> bool:
        return (
            self._request_tier() == "execute"
            and str(self.request_profile.get("intent_type") or "").strip().lower() == "execute_task"
        )

    def _plan_contract_flags(self) -> Dict[str, bool]:
        return {
            "create_required": bool(self.request_profile.get("plan_create_required")),
            "execute_required": bool(self.request_profile.get("plan_execute_required")),
            "execute_after_create_required": bool(
                self.request_profile.get("plan_execute_after_create_required")
            ),
            "review_required": bool(self.request_profile.get("plan_review_required")),
            "optimize_required": bool(self.request_profile.get("plan_optimize_required")),
            "new_requested": bool(self.request_profile.get("plan_new_requested")),
            "conflict_requires_confirmation": bool(
                self.request_profile.get("plan_conflict_requires_confirmation")
            ),
        }

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
        return _gating._detect_partial_completion_in_tool_results(tool_results)

    def _build_partial_completion_retry_nudge(
        self,
        partial_info: Dict[str, Any],
        *,
        task_context: Optional["TaskExecutionContext"],
        user_query: str,
        retry_count: int,
    ) -> str:
        return _gating._build_partial_completion_retry_nudge(
            self,
            partial_info,
            task_context=task_context,
            user_query=user_query,
            retry_count=retry_count,
        )

    def _is_probe_only_execution_cycle(
        self,
        tool_results: List[Dict[str, Any]],
        *,
        task_context: Optional[TaskExecutionContext],
    ) -> bool:
        return _gating._is_probe_only_execution_cycle(
            self,
            tool_results,
            task_context=task_context,
        )

    @staticmethod
    def _is_verification_only_tool_result_cycle(
        tool_results: Sequence[Dict[str, Any]],
    ) -> bool:
        return _gating._is_verification_only_tool_result_cycle(tool_results)

    def _verification_only_cycle_replacement_task_id(
        self,
        executable_calls: Sequence[Any],
        *,
        task_context: Optional[TaskExecutionContext],
        had_real_execution_tool: bool,
    ) -> Optional[int]:
        return _gating._verification_only_cycle_replacement_task_id(
            self,
            executable_calls,
            task_context=task_context,
            had_real_execution_tool=had_real_execution_tool,
        )

    def _build_probe_only_followthrough_nudge(
        self,
        *,
        task_context: Optional[TaskExecutionContext],
        user_query: str,
        stage: int = 1,
    ) -> str:
        return _gating._build_probe_only_followthrough_nudge(
            self,
            task_context=task_context,
            user_query=user_query,
            stage=stage,
        )

    def _task_context_upstream_artifact_paths(
        self,
        task_context: Optional[TaskExecutionContext],
    ) -> List[str]:
        return _gating._task_context_upstream_artifact_paths(self, task_context)

    def _can_force_probe_followthrough_execution(
        self,
        task_context: Optional[TaskExecutionContext],
    ) -> bool:
        return _gating._can_force_probe_followthrough_execution(self, task_context)

    def _build_forced_probe_followthrough_task(
        self,
        *,
        task_context: Optional[TaskExecutionContext],
        user_query: str,
        tool_name: str = "code_executor",
    ) -> str:
        return _gating._build_forced_probe_followthrough_task(
            self,
            task_context=task_context,
            user_query=user_query,
            tool_name=tool_name,
        )

    # Tools that can be used as forced followthrough alternatives to code_executor.
    # These are tools that directly produce outputs (not read-only or coordination tools).
    _FOLLOWTHROUGH_TOOL_CANDIDATES = frozenset({
        "literature_pipeline",
        "web_search",
        "manuscript_writer",
        "review_pack_writer",
        "sequence_fetch",
        "url_fetch",
        "bio_tools",
        "phagescope",
        "deeppl",
        "code_executor",
    })

    @staticmethod
    def _extract_recommended_tool_from_instruction(instruction: str) -> Optional[str]:
        return _gating._extract_recommended_tool_from_instruction(instruction)

    async def _execute_forced_probe_followthrough(
        self,
        *,
        task_context: Optional[TaskExecutionContext],
        user_query: str,
        iteration: int,
        probe_only_execution_cycles: int,
    ) -> Dict[str, Any]:
        return await _gating._execute_forced_probe_followthrough(
            self,
            task_context=task_context,
            user_query=user_query,
            iteration=iteration,
            probe_only_execution_cycles=probe_only_execution_cycles,
        )

    def _build_post_execution_summary_nudge(
        self,
        *,
        task_context: Optional[TaskExecutionContext],
        user_query: str,
        stage: int = 1,
    ) -> str:
        return _gating._build_post_execution_summary_nudge(
            self,
            task_context=task_context,
            user_query=user_query,
            stage=stage,
        )

    def _build_task_handoff_execution_nudge(
        self,
        *,
        task_context: Optional[TaskExecutionContext],
        user_query: str,
        previous_task_id: int,
        next_task_id: int,
    ) -> str:
        return _gating._build_task_handoff_execution_nudge(
            self,
            task_context=task_context,
            user_query=user_query,
            previous_task_id=previous_task_id,
            next_task_id=next_task_id,
        )

    def _can_force_handoff_followthrough_execution(
        self,
        task_context: Optional[TaskExecutionContext],
        *,
        next_task_id: Optional[int],
    ) -> bool:
        return _gating._can_force_handoff_followthrough_execution(
            self,
            task_context,
            next_task_id=next_task_id,
        )

    def _build_forced_handoff_followthrough_task(
        self,
        *,
        task_context: Optional[TaskExecutionContext],
        user_query: str,
        previous_task_id: int,
        next_task_id: int,
    ) -> str:
        return _gating._build_forced_handoff_followthrough_task(
            self,
            task_context=task_context,
            user_query=user_query,
            previous_task_id=previous_task_id,
            next_task_id=next_task_id,
        )

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
        return await _gating._execute_forced_handoff_followthrough(
            self,
            task_context=task_context,
            user_query=user_query,
            iteration=iteration,
            previous_task_id=previous_task_id,
            next_task_id=next_task_id,
            reason=reason,
        )

    def _build_verified_execution_finalize_nudge(
        self,
        *,
        task_context: Optional[TaskExecutionContext],
        user_query: str,
    ) -> str:
        return _gating._build_verified_execution_finalize_nudge(
            self,
            task_context=task_context,
            user_query=user_query,
        )

    def _should_force_verified_execution_finalization(
        self,
        *,
        task_context: Optional[TaskExecutionContext],
        tool_results: Sequence[Dict[str, Any]],
        had_real_execution_tool: bool = False,
    ) -> bool:
        return _gating._should_force_verified_execution_finalization(
            self,
            task_context=task_context,
            tool_results=tool_results,
            had_real_execution_tool=had_real_execution_tool,
        )

    def _build_post_execution_probe_stop_answer(
        self,
        *,
        task_context: Optional[TaskExecutionContext],
        user_query: str,
        steps: Sequence[ThinkingStep],
        tool_results: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> str:
        return _gating._build_post_execution_probe_stop_answer(
            self,
            task_context=task_context,
            user_query=user_query,
            steps=steps,
            tool_results=tool_results,
        )

    def _build_blocked_dependency_answer(
        self,
        *,
        task_context: Optional[TaskExecutionContext],
        user_query: str,
        tool_results: List[Dict[str, Any]],
    ) -> str:
        return _gating._build_blocked_dependency_answer(
            self,
            task_context=task_context,
            user_query=user_query,
            tool_results=tool_results,
        )

    @classmethod
    def _extract_blocked_dependency_clue(cls, item: Dict[str, Any]) -> str:
        return _gating._extract_blocked_dependency_clue(cls, item)

    @classmethod
    def _extract_tool_result_payload(cls, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return _gating._extract_tool_result_payload(cls, item)

    @classmethod
    def _tool_counts_as_real_execution(cls, item: Dict[str, Any]) -> bool:
        return _gating._tool_counts_as_real_execution(cls, item)

    @classmethod
    def _iter_tool_payload_dicts(cls, payload: Any) -> Iterable[Dict[str, Any]]:
        return _gating._iter_tool_payload_dicts(cls, payload)

    @classmethod
    def _payload_dict_indicates_verified_success(cls, candidate: Dict[str, Any]) -> bool:
        return _gating._payload_dict_indicates_verified_success(cls, candidate)

    @classmethod
    def _tool_results_indicate_verified_success(cls, tool_results: Sequence[Dict[str, Any]]) -> bool:
        return _gating._tool_results_indicate_verified_success(cls, tool_results)

    @classmethod
    def _collect_verified_output_refs_from_tool_results(
        cls,
        tool_results: Sequence[Dict[str, Any]],
    ) -> List[str]:
        return _gating._collect_verified_output_refs_from_tool_results(cls, tool_results)

    @classmethod
    def _collect_task_scoped_output_refs_from_tool_results(
        cls,
        tool_results: Sequence[Dict[str, Any]],
        *,
        task_context: Optional[TaskExecutionContext],
    ) -> List[str]:
        return _gating._collect_task_scoped_output_refs_from_tool_results(
            cls,
            tool_results,
            task_context=task_context,
        )

    @classmethod
    def _collect_output_refs_from_tool_results(
        cls,
        tool_results: Sequence[Dict[str, Any]],
    ) -> List[str]:
        return _gating._collect_output_refs_from_tool_results(cls, tool_results)

    @classmethod
    def _is_task_scoped_output_ref(
        cls,
        ref: str,
        task_context: Optional[TaskExecutionContext],
    ) -> bool:
        return _gating._is_task_scoped_output_ref(cls, ref, task_context)

    @classmethod
    def _summarize_tool_payload_for_clue(cls, payload: Any) -> str:
        return _gating._summarize_tool_payload_for_clue(cls, payload)

    @staticmethod
    def _looks_like_blocked_dependency_answer(text: str) -> bool:
        return _gating._looks_like_blocked_dependency_answer(text)

    @staticmethod
    def _looks_like_missing_task_definition_answer(text: str) -> bool:
        return _gating._looks_like_missing_task_definition_answer(text)

    def _should_reject_missing_task_definition_answer(
        self,
        text: str,
        *,
        task_context: Optional[TaskExecutionContext],
    ) -> bool:
        return _gating._should_reject_missing_task_definition_answer(
            self,
            text,
            task_context=task_context,
        )

    def _is_valid_final_answer(self, text: str, *, user_query: str) -> bool:
        return _gating._is_valid_final_answer(self, text, user_query=user_query)

    def _should_retry_external_tool(self, tool_name: str, *, success: bool) -> bool:
        return _gating._should_retry_external_tool(self, tool_name, success=success)

    @staticmethod
    def _try_parse_json_object(raw: Any) -> Optional[Dict[str, Any]]:
        return _gating._try_parse_json_object(raw)

    @classmethod
    def _extract_outcomes_from_step(cls, step: ThinkingStep) -> List[Dict[str, Any]]:
        return _gating._extract_outcomes_from_step(cls, step)

    @classmethod
    def _extract_tool_payloads_from_step(cls, step: ThinkingStep) -> List[Dict[str, Any]]:
        return _gating._extract_tool_payloads_from_step(cls, step)

    @classmethod
    def _collect_tool_failures_from_steps(cls, steps: List[ThinkingStep]) -> List[Dict[str, Any]]:
        return _gating._collect_tool_failures_from_steps(cls, steps)

    def _search_verified_from_steps(self, steps: List[ThinkingStep]) -> bool:
        return _gating._search_verified_from_steps(self, steps)

    def _apply_external_search_notice(
        self,
        answer: str,
        *,
        user_query: str,
        tool_failures: List[Dict[str, Any]],
        search_verified: bool,
    ) -> str:
        return _gating._apply_external_search_notice(
            self,
            answer,
            user_query=user_query,
            tool_failures=tool_failures,
            search_verified=search_verified,
        )

    @classmethod
    def _collect_execute_truth_events(
        cls,
        steps: Sequence[ThinkingStep],
    ) -> List[Dict[str, Any]]:
        return _gating._collect_execute_truth_events(cls, steps)

    def _build_execute_failure_warning(
        self,
        *,
        user_query: str,
        failed_event: Dict[str, Any],
    ) -> str:
        return _gating._build_execute_failure_warning(
            self,
            user_query=user_query,
            failed_event=failed_event,
        )

    def _build_execute_failure_truth_barrier(
        self,
        *,
        user_query: str,
        failed_event: Dict[str, Any],
        profile_text: Optional[str] = None,
    ) -> str:
        return _gating._build_execute_failure_truth_barrier(
            self,
            user_query=user_query,
            failed_event=failed_event,
            profile_text=profile_text,
        )

    def _apply_execute_failure_truth_barrier(
        self,
        answer: str,
        *,
        user_query: str,
        steps: Sequence[ThinkingStep],
    ) -> str:
        return _gating._apply_execute_failure_truth_barrier(
            self,
            answer,
            user_query=user_query,
            steps=steps,
        )

    @classmethod
    def _collect_evidence_scope_signals(cls, steps: Sequence[ThinkingStep]) -> List[Dict[str, Any]]:
        return _gating._collect_evidence_scope_signals(cls, steps)

    def _build_evidence_scope_notice(
        self,
        *,
        user_query: str,
        signals: Sequence[Dict[str, Any]],
        replace_claim: bool = False,
    ) -> str:
        return _gating._build_evidence_scope_notice(
            self,
            user_query=user_query,
            signals=signals,
            replace_claim=replace_claim,
        )

    def _apply_evidence_scope_truth_barrier(
        self,
        answer: str,
        *,
        user_query: str,
        steps: Sequence[ThinkingStep],
    ) -> str:
        return _gating._apply_evidence_scope_truth_barrier(
            self,
            answer,
            user_query=user_query,
            steps=steps,
        )

    @staticmethod
    def _unwrap_tool_result(payload: Dict[str, Any]) -> Dict[str, Any]:
        return _gating._unwrap_tool_result(payload)

    @classmethod
    def _collect_plan_operation_events(cls, steps: List[ThinkingStep]) -> List[Dict[str, Any]]:
        return _gating._collect_plan_operation_events(cls, steps)

    def _summarize_structured_plan_outcome(
        self,
        steps: List[ThinkingStep],
        *,
        user_query: str = "",
    ) -> Dict[str, Any]:
        return _gating._summarize_structured_plan_outcome(
            self,
            steps,
            user_query=user_query,
        )

    def _build_structured_plan_requirement_block(self) -> str:
        return _prompts._build_structured_plan_requirement_block(self)

    @classmethod
    def _extract_successful_created_plan_from_tool_results(
        cls,
        tool_results: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        return _gating._extract_successful_created_plan_from_tool_results(cls, tool_results)

    def _build_created_plan_finalize_nudge(
        self,
        *,
        user_query: str,
        plan_id: int,
        plan_title: Optional[str] = None,
    ) -> str:
        return _prompts._build_created_plan_finalize_nudge(
            self,
            user_query=user_query,
            plan_id=plan_id,
            plan_title=plan_title,
        )

    def _build_created_plan_execute_nudge(
        self,
        *,
        user_query: str,
        plan_id: int,
        plan_title: Optional[str] = None,
    ) -> str:
        return _prompts._build_created_plan_execute_nudge(
            self,
            user_query=user_query,
            plan_id=plan_id,
            plan_title=plan_title,
        )

    def _build_tool_failure_correction_nudge(
        self,
        tool_results: List[Dict[str, Any]],
    ) -> Optional[str]:
        return _prompts._build_tool_failure_correction_nudge(self, tool_results)

    def _build_plan_conflict_confirmation_message(self) -> str:
        return _prompts._build_plan_conflict_confirmation_message(self)

    def _get_structured_plan_retry_prompt(self) -> str:
        return _prompts._get_structured_plan_retry_prompt(self)

    def _ensure_structured_plan_notice(
        self,
        answer: str,
        *,
        outcome: Dict[str, Any],
        user_query: str,
    ) -> str:
        return _gating._ensure_structured_plan_notice(
            self,
            answer,
            outcome=outcome,
            user_query=user_query,
        )

    def _build_structured_plan_contract_failure_answer(
        self,
        *,
        outcome: Dict[str, Any],
        user_query: str,
    ) -> str:
        return _gating._build_structured_plan_contract_failure_answer(
            self,
            outcome=outcome,
            user_query=user_query,
        )

    def _build_request_tier_block(self) -> str:
        return _prompts._build_request_tier_block(self)

    def _build_tool_access_block(self) -> str:
        return _prompts._build_tool_access_block(self)

    def _build_grounded_tooling_block(self) -> str:
        return _prompts._build_grounded_tooling_block(self)

    def _build_artifact_deliverable_workflow_block(self) -> str:
        return _prompts._build_artifact_deliverable_workflow_block(self)

    def _build_bio_tools_quick_map_block(self) -> str:
        return _prompts._build_bio_tools_quick_map_block(self)

    def _build_plan_artifact_discovery_block(
        self,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        return _prompts._build_plan_artifact_discovery_block(self, context)

    def _build_evidence_scope_block(self) -> str:
        return _prompts._build_evidence_scope_block(self)

    def _build_session_isolation_block(self) -> str:
        return _prompts._build_session_isolation_block(self)

    @staticmethod
    def _directory_dataset_analysis_requested(user_query: str) -> bool:
        return _gating._directory_dataset_analysis_requested(user_query)

    @staticmethod
    def _extract_directory_path_from_query(user_query: str) -> Optional[str]:
        return _gating._extract_directory_path_from_query(user_query)

    @classmethod
    def _phagescope_dataset_analysis_requested(cls, user_query: str) -> bool:
        return _gating._phagescope_dataset_analysis_requested(cls, user_query)

    @staticmethod
    def _path_is_generic_tabular_file(path: str) -> bool:
        return _gating._path_is_generic_tabular_file(path)

    @staticmethod
    def _directory_positively_lacks_phagescope_meta_data(path: str) -> bool:
        return _gating._directory_positively_lacks_phagescope_meta_data(path)

    @staticmethod
    def _directory_payload_is_generic_tabular_only(path: str) -> bool:
        return _gating._directory_payload_is_generic_tabular_only(path)

    @classmethod
    def _file_operation_profile_or_census_seen(cls, steps: Sequence[ThinkingStep]) -> bool:
        return _gating._file_operation_profile_or_census_seen(cls, steps)

    @classmethod
    def _phagescope_deep_profile_seen(cls, steps: Sequence[ThinkingStep]) -> bool:
        return _gating._phagescope_deep_profile_seen(cls, steps)

    @classmethod
    def _phagescope_deep_profile_failure(cls, steps: Sequence[ThinkingStep]) -> Optional[str]:
        return _gating._phagescope_deep_profile_failure(cls, steps)

    def _build_phagescope_deep_profile_failure_answer(
        self,
        *,
        user_query: str,
        steps: Sequence[ThinkingStep],
    ) -> Optional[str]:
        return _gating._build_phagescope_deep_profile_failure_answer(
            self,
            user_query=user_query,
            steps=steps,
        )

    def _needs_phagescope_deep_profile_before_final(
        self,
        *,
        user_query: str,
        steps: Sequence[ThinkingStep],
    ) -> Optional[str]:
        return _gating._needs_phagescope_deep_profile_before_final(
            self,
            user_query=user_query,
            steps=steps,
        )

    def _needs_directory_profile_before_final(
        self,
        *,
        user_query: str,
        steps: Sequence[ThinkingStep],
    ) -> Optional[str]:
        return _gating._needs_directory_profile_before_final(
            self,
            user_query=user_query,
            steps=steps,
        )

    def _build_shared_strategy_block(self) -> str:
        return _prompts._build_shared_strategy_block(self)

    def _build_compliance_policy_block(self) -> str:
        return _prompts._build_compliance_policy_block(self)

    def _build_protocol_boundary_block(self, mode: str) -> str:
        return _prompts._build_protocol_boundary_block(self, mode)

    @staticmethod
    def _is_brief_execute_followup_context(context: Optional[Dict[str, Any]]) -> bool:
        return _prompts._is_brief_execute_followup_context(context)

    @staticmethod
    def _append_recent_chat_history(prompt: str, context: Optional[Dict[str, Any]]) -> str:
        return _prompts._append_recent_chat_history(prompt, context)

    @staticmethod
    def _clip_reference_text(value: Any, *, limit: int = 800) -> str:
        return _prompts._clip_reference_text(value, limit=limit)

    @classmethod
    def _append_reference_context(
        cls,
        prompt: str,
        context: Optional[Dict[str, Any]],
    ) -> str:
        return _prompts._append_reference_context(cls, prompt, context)

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
        max_user_query_chars = int(
            getattr(get_settings(), "deep_think_max_user_query_chars", 100000)
        )
        if len(user_query) > max_user_query_chars:
            raise ValueError(
                f"User query too long (max {max_user_query_chars} chars)"
            )

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

        llm_model = (
            getattr(self.llm_client, "model", "")
            or getattr(getattr(self.llm_client, "client", None), "model", "")
            or ""
        )
        try:
            _ctx_budget = int(os.getenv("DEEP_THINK_CONTEXT_BUDGET_TOKENS", "32000") or "32000")
        except (TypeError, ValueError):
            _ctx_budget = 32000
        ctx_mgr = ContextWindowManager(model=llm_model, budget_tokens=max(0, _ctx_budget) or None)

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
        _MAX_PARTIAL_RETRIES = 3
        _MAX_HANDOFF_ITERATION_EXTENSIONS = 4
        last_real_execution_tool_results: List[Dict[str, Any]] = []
        force_verified_execution_finalization = False
        pending_handoff_task_id: Optional[int] = None
        pending_handoff_previous_task_id: Optional[int] = None
        structured_plan_finalize_nudge_plan_id: Optional[int] = None
        runtime_iteration_limit = self.max_iterations
        handoff_iteration_extensions = 0
        consecutive_llm_failures = 0
        max_consecutive_llm_failures = _default_max_consecutive_llm_failures()
        llm_fatal_abort = False
        loop_guard_state: Dict[str, Any] = {
            "verified_deliverables": [],
            "failure_sig_counts": {},
            "failure_sig_warned": set(),
            "last_progress_iteration": 0,
            "no_progress_nudge_sent": False,
            "time_nudge_sent": False,
            "started_at": time.monotonic(),
            "expected_outputs": _derive_expected_outputs(user_query),
        }
        self._produced_deliverable_paths = []
        self._acceptance_missing: List[str] = []
        self._expected_outputs_current = list(loop_guard_state["expected_outputs"])
        if loop_guard_state["expected_outputs"]:
            logger.info(
                "[DEEP_THINK][acceptance] expected deliverable types: %s",
                ",".join(loop_guard_state["expected_outputs"]),
            )

        logger.info("[DEEP_THINK_NATIVE] Starting for: %s", user_query[:50])

        while iteration < runtime_iteration_limit:
            await self._get_pause_event().wait()
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

                update_usage_context(call_purpose="deep_think_iteration", phase="deep_think", tool_name="deep_think")
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
                classified = _classify_llm_provider_error(exc)
                if classified is not None and not classified.retryable:
                    logger.error(
                        "[DEEP_THINK_NATIVE] Non-retryable LLM provider error (%s); aborting run",
                        getattr(classified, "error_code", "unknown"),
                    )
                    consecutive_llm_failures = max_consecutive_llm_failures
                else:
                    consecutive_llm_failures += 1
                if consecutive_llm_failures >= max_consecutive_llm_failures:
                    final_answer = _build_llm_unavailable_final_answer(classified)
                    fallback_used = True
                    llm_fatal_abort = True
                    logger.error(
                        "[DEEP_THINK_NATIVE] Circuit breaker tripped after %d consecutive LLM failure(s); aborting run",
                        consecutive_llm_failures,
                    )
                    break
                continue

            consecutive_llm_failures = 0
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

                    loop_guard_break_reason = self._apply_loop_guards(
                        messages=messages,
                        tool_results=tool_results,
                        iteration=iteration,
                        guard_state=loop_guard_state,
                    )
                    if loop_guard_break_reason:
                        current_step.self_correction = loop_guard_break_reason
                        logger.warning(
                            "[DEEP_THINK][loop-guard] break at iteration=%s: %s",
                            iteration,
                            loop_guard_break_reason,
                        )
                        break

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
                            and structured_plan_finalize_nudge_plan_id != created_plan_id
                        ):
                            plan_title = (
                                created_plan.get("plan_title")
                                if isinstance(created_plan, dict)
                                else None
                            )
                            if self._plan_contract_flags()["execute_after_create_required"]:
                                nudge = self._build_created_plan_execute_nudge(
                                    user_query=user_query,
                                    plan_id=created_plan_id,
                                    plan_title=plan_title,
                                )
                            else:
                                nudge = self._build_created_plan_finalize_nudge(
                                    user_query=user_query,
                                    plan_id=created_plan_id,
                                    plan_title=plan_title,
                                )
                            messages.append(
                                {
                                    "role": "user",
                                    "content": nudge,
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
                        if identical_tool_cycle_count == 1:
                            correction_nudge = self._build_tool_failure_correction_nudge(tool_results)
                            if correction_nudge:
                                messages.append({"role": "user", "content": correction_nudge})
                                logger.info(
                                    "[DEEP_THINK_NATIVE] Injected correction nudge after repeated tool failure"
                                )
                    else:
                        last_tool_cycle_signature = tool_cycle_signature
                        identical_tool_cycle_count = 0

                    if identical_tool_cycle_count >= self.MAX_IDENTICAL_TOOL_CALL_CYCLES:
                        repeated_cycles = identical_tool_cycle_count + 1
                        rep_missing = _missing_expectations(
                            loop_guard_state.get("expected_outputs") or [],
                            loop_guard_state.get("verified_deliverables") or [],
                        )
                        if rep_missing:
                            loop_guard_state["missing_expectations"] = rep_missing
                            self._acceptance_missing = list(rep_missing)
                            logger.warning(
                                "[DEEP_THINK][acceptance] identical-cycle stop with missing deliverable types: %s",
                                ",".join(rep_missing),
                            )
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
                        if rep_missing:
                            final_answer += (
                                "\n\nNote: the requested deliverable type(s) are still missing: "
                                + ", ".join(rep_missing)
                                + "."
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
                        probe_limit = 6 if had_real_execution_tool else 12
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
                                    raw_fallback = self._build_post_execution_probe_stop_answer(
                                        task_context=task_context,
                                        user_query=user_query,
                                        steps=[*thinking_steps, current_step],
                                        tool_results=last_real_execution_tool_results,
                                    )
                                    # Try to synthesize a clean answer via LLM instead of
                                    # dumping raw evidence snippets to the user.
                                    try:
                                        synthesized = await self._generate_fallback_from_evidence(
                                            user_query=user_query,
                                            evidence_snippets=raw_fallback,
                                            steps=[*thinking_steps, current_step],
                                            task_context=task_context,
                                            max_retries=2,
                                            timeout=120,
                                            max_tokens=4000,
                                        )
                                        if len(synthesized) >= 100 or len(synthesized) >= len(raw_fallback) // 2:
                                            final_answer = synthesized
                                        else:
                                            final_answer = raw_fallback
                                    except Exception as synth_exc:
                                        logger.warning(
                                            "Post-execution probe-stop LLM synthesis failed, using raw fallback: %s",
                                            str(synth_exc)[:200],
                                        )
                                        final_answer = raw_fallback
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
                        profile_path = self._needs_directory_profile_before_final(
                            user_query=user_query,
                            steps=thinking_steps,
                        )
                        if profile_path:
                            current_step.self_correction = (
                                "Rejected a directory/dataset final answer until file_operations profile evidence is collected."
                            )
                            final_answer = ""
                            thinking_steps.append(current_step)
                            if self.on_thinking:
                                await self._safe_callback(current_step)
                            forced_call = SimpleNamespace(
                                name="file_operations",
                                id=f"forced_directory_profile_{iteration}",
                                arguments={"operation": "profile", "path": profile_path},
                            )
                            forced_result = await self._execute_native_tool_call(
                                tc=forced_call,
                                iteration=iteration,
                                index=9998,
                            )
                            if "file_operations" not in tools_used:
                                tools_used.append("file_operations")
                            forced_step = ThinkingStep(
                                iteration=iteration,
                                thought="Collecting required directory profile evidence before final answer.",
                                action=json.dumps(
                                    {
                                        "tool": "file_operations",
                                        "params": {"operation": "profile", "path": profile_path},
                                    },
                                    ensure_ascii=False,
                                ),
                                action_result=None,
                                self_correction="Forced directory profile/census evidence for dataset-level analysis.",
                                kind="tool",
                                status="calling_tool",
                            )
                            thinking_steps.append(forced_step)
                            self._append_tool_cycle_messages(
                                messages=messages,
                                tool_results=[forced_result],
                                assistant_content="",
                                current_step=forced_step,
                            )
                            forced_step.status = "analyzing"
                            if self.on_thinking:
                                await self._safe_callback(forced_step)
                            messages.append(
                                {
                                    "role": "user",
                                    "content": (
                                        "A directory-level file_operations profile was required and has now been collected. "
                                        "Use its evidence_scope/status_counts/status_count_sources in the final answer. "
                                        "Do not make all/every/global-ready claims unless the profile supports them. "
                                        "Now call submit_final_answer with a scoped dataset summary."
                                    ),
                                }
                            )
                            continue
                        phagescope_path = self._needs_phagescope_deep_profile_before_final(
                            user_query=user_query,
                            steps=thinking_steps,
                        )
                        if phagescope_path:
                            current_step.self_correction = (
                                "Rejected a PhageScope dataset final answer until phagescope_research deep_profile evidence is collected."
                            )
                            final_answer = ""
                            thinking_steps.append(current_step)
                            if self.on_thinking:
                                await self._safe_callback(current_step)
                            forced_call = SimpleNamespace(
                                name="phagescope_research",
                                id=f"forced_phagescope_deep_profile_{iteration}",
                                arguments={"action": "deep_profile", "data_dir": phagescope_path},
                            )
                            forced_result = await self._execute_native_tool_call(
                                tc=forced_call,
                                iteration=iteration,
                                index=9997,
                            )
                            if "phagescope_research" not in tools_used:
                                tools_used.append("phagescope_research")
                            forced_step = ThinkingStep(
                                iteration=iteration,
                                thought="Collecting required PhageScope deep profile evidence before final answer.",
                                action=json.dumps(
                                    {
                                        "tool": "phagescope_research",
                                        "params": {"action": "deep_profile", "data_dir": phagescope_path},
                                    },
                                    ensure_ascii=False,
                                ),
                                action_result=None,
                                self_correction="Forced PhageScope deep_profile evidence for dataset-level analysis.",
                                kind="tool",
                                status="calling_tool",
                            )
                            thinking_steps.append(forced_step)
                            self._append_tool_cycle_messages(
                                messages=messages,
                                tool_results=[forced_result],
                                assistant_content="",
                                current_step=forced_step,
                            )
                            forced_step.status = "analyzing"
                            if self.on_thinking:
                                await self._safe_callback(forced_step)
                            messages.append(
                                {
                                    "role": "user",
                                    "content": (
                                        "A PhageScope deep_profile was required and has now been collected. "
                                        "Use metadata_size_bytes/metadata_size_human for meta_data size claims, "
                                        "total_size_bytes/total_size_human for whole-dataset size claims, and "
                                        "metadata_schema/ml_metadata_table/label_quality/split_readiness for readiness claims. "
                                        "Do not invent numeric size, row, column, or data-readiness claims absent from deep_profile. "
                                        "Now call submit_final_answer with an evidence-bound PhageScope dataset summary."
                                    ),
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
                        _tier_for_early_stop == "standard"
                        and _content_for_early_stop
                        and len(_content_for_early_stop) >= 20
                        and not self._is_execute_task_request()
                        and not self._PROCESS_NARRATION_RE.match(_content_for_early_stop)
                        and not self._collect_tool_failures_from_steps(thinking_steps)
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
                messages.append({
                    "role": "user",
                    "content": (
                        "IMPORTANT: You are about to reach the maximum number of thinking steps. "
                        "You MUST call submit_final_answer on the NEXT step with the best answer you can provide "
                        "based on all evidence gathered so far. Do NOT continue researching — synthesize NOW."
                    ),
                })

        if final_answer and not llm_fatal_abort and not self._is_valid_final_answer(final_answer, user_query=user_query):
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
            _block_reason = str((context or {}).get("explicit_scope_block_reason") or "blocked_deps").strip().lower()
            if _block_reason == "all_completed":
                final_answer = (
                    f"Tasks [{_id_str}] are already completed — "
                    f"no re-execution is needed. "
                    f"If you want to re-run them, please say so explicitly."
                )
            else:
                final_answer = (
                    f"Tasks [{_id_str}] could not be executed in this turn: "
                    f"all tasks in the explicit set are blocked by unmet out-of-scope dependencies. "
                    f"Please check the dependency status of the listed tasks and retry "
                    f"after resolving any upstream blockers."
                )
            fallback_used = True
            logger.info(
                "[DEEP_THINK_NATIVE] explicit_scope_all_blocked: skipping forced synthesis, "
                "emitting structured blocker for task_ids=%s reason=%s",
                _blocked_ids,
                _block_reason,
            )
            if self.on_final_delta:
                await self._stream_final_answer(final_answer)
        elif not final_answer and thinking_steps:
            phagescope_failure_answer = self._build_phagescope_deep_profile_failure_answer(
                user_query=user_query,
                steps=thinking_steps,
            )
            if phagescope_failure_answer:
                logger.warning(
                    "[DEEP_THINK_NATIVE] Blocking forced synthesis after failed PhageScope deep_profile."
                )
                final_answer = phagescope_failure_answer
            else:
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
        evidence_scope_answer = self._apply_evidence_scope_truth_barrier(
            final_answer,
            user_query=user_query,
            steps=thinking_steps,
        )
        if evidence_scope_answer != final_answer:
            fallback_used = True
        final_answer = evidence_scope_answer
        structured_plan_outcome = self._summarize_structured_plan_outcome(
            thinking_steps,
            user_query=user_query,
        )
        if structured_plan_outcome.get("required") and not structured_plan_outcome.get("satisfied"):
            final_answer = self._build_structured_plan_contract_failure_answer(
                outcome=structured_plan_outcome,
                user_query=user_query,
            )
            fallback_used = True
        else:
            final_answer = self._ensure_structured_plan_notice(
                final_answer,
                outcome=structured_plan_outcome,
                user_query=user_query,
            )
        final_answer = sanitize_professional_response_text(final_answer)
        final_answer = _ensure_inline_images(final_answer, self._collect_inline_image_relpaths())

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
        return _prompts._build_native_system_prompt(self, context, task_context)

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
            await self._get_pause_event().wait()
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
                        if identical_tool_cycle_count == 1:
                            correction_nudge = self._build_tool_failure_correction_nudge(cycle_results)
                            if correction_nudge:
                                messages.append({"role": "user", "content": correction_nudge})
                                logger.info(
                                    "[DEEP_THINK_NATIVE] Injected correction nudge after repeated tool failure"
                                )
                    else:
                        last_tool_cycle_signature = tool_cycle_signature
                        identical_tool_cycle_count = 0

                    if identical_tool_cycle_count >= self.MAX_IDENTICAL_TOOL_CALL_CYCLES:
                        repeated_cycles = identical_tool_cycle_count + 1
                        rep_missing = _missing_expectations(
                            getattr(self, "_expected_outputs_current", None) or [],
                            getattr(self, "_produced_deliverable_paths", None) or [],
                        )
                        if rep_missing:
                            self._acceptance_missing = list(rep_missing)
                            logger.warning(
                                "[DEEP_THINK][acceptance] identical-cycle stop with missing deliverable types: %s",
                                ",".join(rep_missing),
                            )
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
                        if rep_missing:
                            final_answer += (
                                "\n\nNote: the requested deliverable type(s) are still missing: "
                                + ", ".join(rep_missing)
                                + "."
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
        if structured_plan_outcome.get("required") and not structured_plan_outcome.get("satisfied"):
            final_answer = self._build_structured_plan_contract_failure_answer(
                outcome=structured_plan_outcome,
                user_query=user_query,
            )
            fallback_used = True
        else:
            final_answer = self._ensure_structured_plan_notice(
                final_answer,
                outcome=structured_plan_outcome,
                user_query=user_query,
            )
        final_answer = sanitize_professional_response_text(final_answer)
        final_answer = _ensure_inline_images(final_answer, self._collect_inline_image_relpaths())

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
        return _dispatch._normalize_tool_callback_outcome(result)

    def _extract_artifact_paths(self, tool_name: str, result: Any) -> List[str]:
        return _dispatch._extract_artifact_paths(self, tool_name, result)

    @staticmethod
    def _is_internal_artifact_path(path: str) -> bool:
        return _dispatch._is_internal_artifact_path(path)

    @staticmethod
    def _looks_like_artifact_path(path: str) -> bool:
        return _dispatch._looks_like_artifact_path(path)

    def _extract_explicit_artifact_paths(self, result: Any) -> List[str]:
        return _dispatch._extract_explicit_artifact_paths(self, result)

    async def _emit_artifacts(self, tool_name: str, result: Any, iteration: int) -> None:
        return await _dispatch._emit_artifacts(self, tool_name, result, iteration)

    def _extract_guard_candidates(self, tool_results: List[Dict[str, Any]]) -> List[str]:
        return _guards._extract_guard_candidates(self, tool_results)

    def _verify_guard_path(self, candidate: str) -> Optional[str]:
        return _guards._verify_guard_path(self, candidate)

    def _collect_inline_image_relpaths(self, limit: int = 8) -> List[str]:
        return _guards._collect_inline_image_relpaths(self, limit)

    def _failure_signature_for_result(self, item: Dict[str, Any]) -> Optional[str]:
        return _guards._failure_signature_for_result(self, item)

    def _loop_guard_endgame_armed(self) -> bool:
        return _guards._loop_guard_endgame_armed(self)

    def _apply_loop_guards(
        self,
        *,
        messages: List[Dict[str, Any]],
        tool_results: List[Dict[str, Any]],
        iteration: int,
        guard_state: Dict[str, Any],
    ) -> Optional[str]:
        return _guards._apply_loop_guards(
            self,
            messages=messages,
            tool_results=tool_results,
            iteration=iteration,
            guard_state=guard_state,
        )

    async def _execute_native_tool_call(
        self,
        tc: Any,
        iteration: int,
        index: int,
    ) -> Dict[str, Any]:
        return await _dispatch._execute_native_tool_call(self, tc, iteration, index)

    def _extract_evidence(
        self,
        tool_name: str,
        tool_params: Dict[str, Any],
        tool_result: Any,
    ) -> List[Dict[str, str]]:
        return _dispatch._extract_evidence(self, tool_name, tool_params, tool_result)

    @staticmethod
    def _build_tool_callback_summary(result: Any) -> str:
        return _dispatch._build_tool_callback_summary(result)

    @classmethod
    def _build_tool_result_text_for_llm(
        cls,
        *,
        tool_name: str,
        result: Any,
        success: bool,
        error: Any,
    ) -> str:
        return _dispatch._build_tool_result_text_for_llm(
            cls,
            tool_name=tool_name,
            result=result,
            success=success,
            error=error,
        )

    @classmethod
    def _compact_tool_result_for_llm(
        cls, tool_name: str, result: Any
    ) -> Optional[Dict[str, Any]]:
        return _dispatch._compact_tool_result_for_llm(cls, tool_name, result)

    @classmethod
    def _compact_code_executor_result_for_llm(
        cls, result: Any
    ) -> Optional[Dict[str, Any]]:
        return _dispatch._compact_code_executor_result_for_llm(cls, result)

    @classmethod
    def _compact_phagescope_research_result_for_llm(
        cls, result: Any
    ) -> Optional[Dict[str, Any]]:
        return _dispatch._compact_phagescope_research_result_for_llm(cls, result)

    @classmethod
    def _compact_file_operations_result_for_llm(
        cls, result: Any
    ) -> Optional[Dict[str, Any]]:
        return _dispatch._compact_file_operations_result_for_llm(cls, result)

    @staticmethod
    def _append_tool_cycle_messages(
        *,
        messages: List[Dict[str, Any]],
        tool_results: List[Dict[str, Any]],
        assistant_content: str,
        current_step: "ThinkingStep",
    ) -> None:
        return _dispatch._append_tool_cycle_messages(
            messages=messages,
            tool_results=tool_results,
            assistant_content=assistant_content,
            current_step=current_step,
        )

    @staticmethod
    def _contains_tool(tool_results: List[Dict[str, Any]], tool_name: str) -> bool:
        return _dispatch._contains_tool(tool_results, tool_name)

    @classmethod
    def _build_tool_cycle_signature(cls, tool_results: List[Dict[str, Any]]) -> str:
        return _dispatch._build_tool_cycle_signature(cls, tool_results)

    @classmethod
    def _extract_tool_result_marker(cls, tool_name: str, tool_result_text: Any) -> str:
        return _dispatch._extract_tool_result_marker(cls, tool_name, tool_result_text)

    @staticmethod
    def _normalize_marker_text(raw_text: str) -> str:
        return _dispatch._normalize_marker_text(raw_text)

    @classmethod
    def _extract_phagescope_state(cls, tool_result_text: str) -> Optional[Dict[str, Any]]:
        return _dispatch._extract_phagescope_state(cls, tool_result_text)

    @classmethod
    def _build_repetition_stop_answer(
        cls,
        tool_results: List[Dict[str, Any]],
        repeated_cycles: int,
    ) -> str:
        return _dispatch._build_repetition_stop_answer(cls, tool_results, repeated_cycles)

    @staticmethod
    def _clip_log_text(value: Any, *, limit: int = 400) -> str:
        return _dispatch._clip_log_text(value, limit=limit)

    @classmethod
    def _sanitize_tool_params_for_log(cls, params: Any) -> str:
        return _dispatch._sanitize_tool_params_for_log(cls, params)

    @classmethod
    def _chunk_final_answer(cls, text: str) -> List[str]:
        return _dispatch._chunk_final_answer(cls, text)

    async def _stream_final_answer(self, final_answer: str) -> None:
        return await _dispatch._stream_final_answer(self, final_answer)

    def _build_system_prompt(
        self,
        context: Optional[Dict[str, Any]] = None,
        task_context: Optional[TaskExecutionContext] = None,
    ) -> str:
        return _prompts._build_system_prompt(self, context, task_context)

    def _get_next_step_prompt(self, iteration: int) -> str:
        return _prompts._get_next_step_prompt(self, iteration)

    def _parse_llm_response_safe(self, response: str) -> tuple[Dict[str, Any], Optional[str]]:
        return _protocol._parse_llm_response_safe(self, response)

    def _parse_llm_response(self, response: str) -> Dict[str, Any]:
        return _protocol._parse_llm_response(self, response)

    def _repair_json_text(self, text: str) -> str:
        return _protocol._repair_json_text(self, text)

    def _regex_parse_fallback(self, text: str) -> Optional[Dict[str, Any]]:
        return _protocol._regex_parse_fallback(self, text)

    @staticmethod
    def _try_parse_structured_actions(content: str) -> List[Dict[str, Any]]:
        return _protocol._try_parse_structured_actions(content)

    def _extract_json(self, text: str) -> str:
        return _protocol._extract_json(self, text)

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
        return _synthesis._collect_tool_usage_counts(cls, steps)

    @staticmethod
    def _format_tool_usage_counts(counts: Dict[str, int]) -> str:
        return _synthesis._format_tool_usage_counts(counts)

    @staticmethod
    def _select_steps_for_summary(steps: List[ThinkingStep]) -> List[ThinkingStep]:
        return _synthesis._select_steps_for_summary(steps)

    @staticmethod
    def _slim_evidence_text_for_synthesis(text: str) -> str:
        return _synthesis._slim_evidence_text_for_synthesis(text)

    def _collect_evidence_snippets(
        self,
        steps: List[ThinkingStep],
        *,
        max_steps: int = 8,
        max_chars: int = 3000,
        per_snippet_max: int = 900,
    ) -> str:
        return _synthesis._collect_evidence_snippets(
            self,
            steps,
            max_steps=max_steps,
            max_chars=max_chars,
            per_snippet_max=per_snippet_max,
        )

    @staticmethod
    def _humanize_single_tool_result(tool_name: str, obj: dict) -> str:
        return _synthesis._humanize_single_tool_result(tool_name, obj)

    def _collect_user_facing_evidence_snippets(
        self,
        steps: List[ThinkingStep],
        *,
        max_steps: int = 8,
        max_chars: int = 3000,
        per_snippet_max: int = 900,
    ) -> str:
        return _synthesis._collect_user_facing_evidence_snippets(
            self,
            steps,
            max_steps=max_steps,
            max_chars=max_chars,
            per_snippet_max=per_snippet_max,
        )

    def _build_bound_execute_task_fallback(
        self,
        steps: List[ThinkingStep],
        *,
        user_query: str,
        task_context: Optional[TaskExecutionContext],
    ) -> str:
        return _synthesis._build_bound_execute_task_fallback(
            self,
            steps,
            user_query=user_query,
            task_context=task_context,
        )

    def _build_structured_fallback(self, steps: List[ThinkingStep], user_query: str = "") -> str:
        return _synthesis._build_structured_fallback(self, steps, user_query)

    async def _chat_text_streaming(self, prompt: str, *, max_tokens: int) -> str:
        return await _synthesis._chat_text_streaming(self, prompt, max_tokens=max_tokens)

    async def _generate_fallback_from_evidence(
        self,
        user_query: str,
        evidence_snippets: str,
        steps: List[ThinkingStep],
        task_context: Optional[TaskExecutionContext] = None,
        *,
        max_retries: int = 3,
        timeout: Optional[float] = None,
        max_tokens: int = 2000,
    ) -> str:
        return await _synthesis._generate_fallback_from_evidence(
            self,
            user_query,
            evidence_snippets,
            steps,
            task_context,
            max_retries=max_retries,
            timeout=timeout,
            max_tokens=max_tokens,
        )

    async def _forced_synthesis_from_steps(
        self,
        steps: List[ThinkingStep],
        user_query: str,
        messages: List[Dict[str, Any]],
        task_context: Optional[TaskExecutionContext] = None,
    ) -> str:
        return await _synthesis._forced_synthesis_from_steps(
            self,
            steps,
            user_query,
            messages,
            task_context,
        )

    async def _fallback_answer_from_steps(
        self,
        steps: List[ThinkingStep],
        user_query: str = "",
        task_context: Optional[TaskExecutionContext] = None,
    ) -> str:
        return await _synthesis._fallback_answer_from_steps(
            self,
            steps,
            user_query,
            task_context,
        )

    async def _generate_summary(self, steps: List[ThinkingStep], user_query: str) -> str:
        return await _synthesis._generate_summary(self, steps, user_query)
