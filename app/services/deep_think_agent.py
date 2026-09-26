import asyncio
import json
import logging
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from app.services.deep_think import controller as _controller
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
    _resolve_context_budget_tokens,
    _default_synthesis_max_tokens,
    _default_synthesis_timeout_seconds,
    _acceptance_v2_enabled,
    _acceptance_v2_max_tokens,
    _acceptance_v2_timeout_seconds,
    _derive_expected_outputs,
    _drop_process_echo_bullets,
    _ensure_inline_images,
    _failure_signature_break_count,
    _failure_signature_warn_count,
    _guard_json_payload,
    _looks_like_cli_protocol_json,
    _missing_expectations,
    _missing_expectations_detailed,
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
    if tool_name in {"bio_tools", "phagescope", "sequence_fetch", "url_fetch"}:
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
    # Process patterns only mark an answer as process-only in short replies; in
    # a developed answer the same words ("我先说结论…") introduce substance, and
    # substring-matching the whole text would reject good final answers.
    if len(raw) <= 120 and any(pattern in lowered for pattern in _PROCESS_ONLY_PATTERNS):
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

    @staticmethod
    def _cycle_is_readonly_verification(tool_results: List[Dict[str, Any]]) -> bool:
        return _gating._cycle_is_readonly_verification(tool_results)

    def _build_readonly_verification_redirect_nudge(
        self,
        *,
        user_query: str,
        count: int,
    ) -> str:
        return _gating._build_readonly_verification_redirect_nudge(
            self,
            user_query=user_query,
            count=count,
        )

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
        return await _controller.think(self, user_query, context, task_context)

    # ------------------------------------------------------------------ #
    #  Native tool calling path                                           #
    # ------------------------------------------------------------------ #

    async def _think_native(
        self,
        user_query: str,
        context: Optional[Dict[str, Any]] = None,
        task_context: Optional[TaskExecutionContext] = None,
    ) -> DeepThinkResult:
        return await _controller._think_native(self, user_query, context, task_context)

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
        return await _controller._think_prompt_based(self, user_query, context, task_context)

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
