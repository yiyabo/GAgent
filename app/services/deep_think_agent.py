import asyncio
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from app.services.execution.tool_executor import UnifiedToolExecutor
from app.services.foundation.settings import get_settings
from app.services.response_style import sanitize_professional_response_text
from app.services.tool_schemas import build_tool_schemas
from app.llm import update_usage_context
from app.services.deep_think import guards as _guards
from app.services.deep_think import prompts as _prompts
from app.services.deep_think import protocol as _protocol
from app.services.deep_think import synthesis as _synthesis
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


_INTERNAL_ARTIFACT_FILENAMES = {"result.json", "manifest.json", "preview.json"}
_INTERNAL_TOOL_OUTPUT_RE = re.compile(
    r"/job_[^/]+/step_\d+_[^/]+/(?:result|manifest|preview)\.json$",
    re.IGNORECASE,
)
_ARTIFACT_PATH_EXTS = {
    ".bib",
    ".csv",
    ".docx",
    ".fa",
    ".faa",
    ".fasta",
    ".gb",
    ".gbk",
    ".gif",
    ".h5ad",
    ".html",
    ".ipynb",
    ".jpeg",
    ".jpg",
    ".json",
    ".md",
    ".mmd",
    ".nwk",
    ".pdf",
    ".pkl",
    ".png",
    ".pptx",
    ".py",
    ".r",
    ".rds",
    ".svg",
    ".tex",
    ".tsv",
    ".txt",
    ".webp",
    ".xlsx",
    ".xls",
    ".xml",
    ".yaml",
    ".yml",
    ".zip",
}
# Native tool steps often prefix JSON: "[file_operations] {...}"
_EXPLORATORY_FILE_OPERATIONS = {"read", "list", "profile", "census", "exists", "info"}
_GLOBAL_SCOPE_WORD_RE = re.compile(r"\b(?:all|every|each)\b|全部|所有|每个", re.IGNORECASE)
_SUCCESS_WORD_RE = re.compile(
    r"\b(?:complete(?:d)?|success(?:ful|fully)?|succeeded|finished|done)\b|完成|成功|已完成|已成功",
    re.IGNORECASE,
)
_FAILURE_WORD_RE = re.compile(
    r"\b(?:fail(?:ed|ure|ures)?|incomplete|not\s+all|partial)\b|失败|不完整|未完成|并非全部|不是全部",
    re.IGNORECASE,
)
_NEGATED_GLOBAL_SUCCESS_RE = re.compile(
    r"\b(?:not|no)\s+(?:all|every|each)\b|并非(?:全部|所有|每个)|不是(?:全部|所有|每个)|并不是(?:全部|所有|每个)",
    re.IGNORECASE,
)
_DIRECTORY_DATASET_REQUEST_RE = re.compile(
    r"\b(?:analy[sz]e|inspect|audit|summari[sz]e|review|look|profile|census|folder|directory|dataset|data\s+folder|output\s+folder)\b|分析|查看|检查|审计|总结|目录|文件夹|数据集",
    re.IGNORECASE,
)
_PHAGESCOPE_DATASET_REQUEST_RE = re.compile(
    r"\b(?:phagescope|phage[-\s]?host|host\s+prediction|data\s+splitting|model\s+selection|benchmarking|biological\s+validation)\b|噬菌体|宿主预测|数据划分|模型选择|生物学验证",
    re.IGNORECASE,
)
_PHAGESCOPE_ANALYSIS_ACTION_RE = re.compile(
    r"\b(?:analy[sz]e|inspect|audit|summari[sz]e|review|look|profile|explore|start|dataset|data)\b|分析|查看|看看|检查|审计|总结|探索|数据集|数据",
    re.IGNORECASE,
)
# Generic spreadsheet/tabular files (Excel/CSV/Parquet) under a "phagescope"
# directory are NOT PhageScope datasets. ``.tsv``/``.txt`` are deliberately
# excluded here because those ARE real PhageScope metadata formats.
_NON_PHAGESCOPE_TABULAR_FILE_EXTS = (
    ".xlsx",
    ".xls",
    ".xlsm",
    ".csv",
    ".parquet",
    ".feather",
)
# Match real absolute filesystem paths without treating the slash inside
# relative output paths such as ``results/figures/目录`` as ``/figures/目录``.
_ABSOLUTE_PATH_RE = re.compile(r"(?<![A-Za-z0-9_.-])/(?:[^\s`\"'<>，。；;])+")
_UNVERIFIED_SAMPLE_DIRECTORY_CLAIM_RE = re.compile(
    r"(?:total\s+sample\s+director(?:y|ies)|sample\s+director(?:y|ies)\s*[:|]\s*[\d,]+|样本目录\s*[:：|]?\s*[\d,]+)",
    re.IGNORECASE,
)
_UNVERIFIED_EACH_FILE_STRUCTURE_RE = re.compile(
    r"(?:each|every|all)\s+(?:completed\s+)?(?:sample|sample\s+directory|sample\s+folder)[^\n.]{0,120}\b(?:contain|contains|has|have)\b|每个(?:已完成)?样本[^\n。]{0,80}(?:包含|含有|都有)",
    re.IGNORECASE,
)
_UNSUPPORTED_RETRY_RERUN_RE = re.compile(
    r"\b(?:retr(?:y|ied|ies)|rerun|re-run|multiple\s+pipeline\s+runs|logged\s+across\s+multiple)\b|重试|重新运行|多次运行|多轮运行",
    re.IGNORECASE,
)


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


def _looks_like_global_success_claim_text(reply_text: str) -> bool:
    text = str(reply_text or "").strip()
    if not text:
        return False
    if _NEGATED_GLOBAL_SUCCESS_RE.search(text):
        return False
    return bool(_GLOBAL_SCOPE_WORD_RE.search(text) and _SUCCESS_WORD_RE.search(text))


def _answer_acknowledges_failed_status_counts(
    reply_text: str,
    signals: Sequence[Dict[str, Any]],
) -> bool:
    text = str(reply_text or "").strip()
    if not text or not _FAILURE_WORD_RE.search(text):
        return False
    failed_counts: set[int] = set()
    for signal in signals:
        status_counts = signal.get("status_counts") if isinstance(signal.get("status_counts"), dict) else {}
        failed = status_counts.get("failed")
        if isinstance(failed, int) and failed > 0:
            failed_counts.add(failed)
        sources = signal.get("status_count_sources") if isinstance(signal.get("status_count_sources"), list) else []
        for source in sources:
            if not isinstance(source, dict) or source.get("kind") != "failure":
                continue
            entry_count = source.get("entry_count")
            if isinstance(entry_count, int) and entry_count > 0:
                failed_counts.add(entry_count)
    return bool(failed_counts and any(str(count) in text for count in failed_counts))


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
        """Scan tool_results for a code_executor call that flagged partial completion.

        Returns a dict with failure context when partial completion is detected;
        otherwise ``None``.
        """
        for item in tool_results:
            if item.get("tool_name") != "code_executor":
                continue
            raw_text = item.get("tool_result_text") or ""
            try:
                payload = json.loads(raw_text) if isinstance(raw_text, str) else raw_text
            except (JSONDecodeError, TypeError):
                continue
            inner = payload
            if isinstance(payload, dict) and "result" in payload and isinstance(payload["result"], dict):
                inner = payload["result"]
            if not isinstance(inner, dict):
                continue
            if inner.get("partial_completion_suspected"):
                stderr = str(inner.get("stderr") or "")
                error_summary = str(inner.get("error_summary") or "")
                contract_error_summary = str(inner.get("contract_error_summary") or "")
                contract_diff = inner.get("contract_diff")
                missing_outputs: List[str] = []
                if isinstance(contract_diff, dict):
                    missing_outputs = list(contract_diff.get("missing_required_outputs") or [])
                verification = inner.get("verification")
                verification_failures: List[str] = []
                if isinstance(verification, dict):
                    for f in verification.get("failures", []):
                        if isinstance(f, dict):
                            msg = str(f.get("message") or f.get("type") or "")
                            if msg:
                                verification_failures.append(msg[:200])
                error_snippet = stderr[:500] if stderr else ""
                if error_summary:
                    error_snippet = error_summary + ("\n" + error_snippet if error_snippet else "")
                if contract_error_summary and contract_error_summary not in error_snippet:
                    error_snippet = contract_error_summary + ("\n" + error_snippet if error_snippet else "")
                return {
                    "partial_ratio": inner.get("partial_ratio"),
                    "produced_files": inner.get("produced_files", []),
                    "task_directory_full": inner.get("task_directory_full", ""),
                    "error_snippet": error_snippet[:800],
                    "missing_outputs": missing_outputs[:10],
                    "verification_failures": verification_failures[:5],
                    "failure_kind": str(inner.get("failure_kind") or ""),
                    "exit_code": inner.get("exit_code"),
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
        language = detect_reasoning_language(user_query)
        ratio = partial_info.get("partial_ratio", "?/?")
        produced = partial_info.get("produced_files", [])
        task_dir = partial_info.get("task_directory_full", "")
        error_snippet = str(partial_info.get("error_snippet") or "")
        missing_outputs = partial_info.get("missing_outputs", [])
        verification_failures = partial_info.get("verification_failures", [])
        failure_kind = str(partial_info.get("failure_kind") or "")

        produced_hint = ""
        if produced:
            names = [p.rsplit("/", 1)[-1] if "/" in p else p for p in produced[:15]]
            produced_hint = ", ".join(names)
            if len(produced) > 15:
                produced_hint += f" … ({len(produced)} total)"

        missing_hint = ""
        if missing_outputs:
            missing_hint = ", ".join(missing_outputs[:10])
        elif verification_failures:
            missing_hint = "; ".join(verification_failures[:3])

        task_bits: List[str] = []
        if task_context:
            if task_context.task_id is not None:
                task_bits.append(f"Task ID={task_context.task_id}")
            if task_context.task_name:
                task_bits.append(f"Task Name={task_context.task_name}")
        task_label = " | ".join(task_bits) if task_bits else ""

        error_block = ""
        if error_snippet:
            error_block = f"\n🔴 **上次错误信息**:\n```\n{error_snippet}\n```"
        if missing_hint:
            error_block += f"\n❌ **缺失产出**: {missing_hint}"

        if retry_count >= 3:
            zh = (
                f"⚠️ 这是第 {retry_count} 次重试，之前的方案持续失败。\n"
                f"{error_block}\n"
                f"请**停止重试相同的方法**。要么：\n"
                f"1. 用完全不同的技术路线完成任务（换库、换算法、简化流程）\n"
                f"2. 如果任务确实无法完成，提交最终答案，说明失败原因和已尝试的方法，标记任务为失败。\n"
            )
            en = (
                f"⚠️ Retry #{retry_count}. Previous approaches keep failing.\n"
                f"{error_block}\n"
                f"**Stop retrying the same approach.** Either:\n"
                f"1. Use a fundamentally different technique (different library, algorithm, or simplified pipeline).\n"
                f"2. If the task is genuinely infeasible, submit a final answer explaining what was attempted, "
                f"why it failed, and mark the task as failed.\n"
            )
        elif retry_count >= 2:
            zh = (
                f"⚠️ 同样的错误第 2 次出现。上次代码执行只完成了 {ratio} 项。\n"
                f"{error_block}\n"
                f"请**先分析错误原因**，然后**尝试不同的方法**（不要重复相同的代码）。\n"
                f"已产出文件: {produced_hint or '无'}\n"
                f"如果当前方法行不通，考虑替代方案。\n"
            )
            en = (
                f"⚠️ Same error appeared twice. Last run only completed {ratio} items.\n"
                f"{error_block}\n"
                f"**Analyze the error first**, then **try a different approach** (do NOT repeat the same code).\n"
                f"Files produced so far: {produced_hint or 'none'}\n"
                f"Consider alternative techniques if the current one is not working.\n"
            )
        else:
            zh = (
                f"⚠️ 上次 code_executor 执行检测到**部分完成**：仅处理了 {ratio} 项。\n"
                f"{error_block}\n"
                f"已产出文件: {produced_hint or '无'}\n"
                f"请按以下步骤操作：\n"
                f"1. 先**诊断失败原因**（查看上面的错误信息），理解为什么部分产出缺失。\n"
                f"2. 修复根因后调用 `code_executor`，仅处理尚未完成的剩余项。\n"
                f"3. 新结果追加到 results/ 目录，不要覆盖已有文件。\n"
            )
            en = (
                f"⚠️ Previous code_executor run: **partial completion** ({ratio} items).\n"
                f"{error_block}\n"
                f"Files produced: {produced_hint or 'none'}\n"
                f"Steps:\n"
                f"1. **Diagnose the failure** (see error above) — understand WHY some outputs are missing.\n"
                f"2. Fix the root cause, then call `code_executor` for remaining items only.\n"
                f"3. Append new results to results/ — do NOT overwrite existing files.\n"
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
            raw_paths = []
            for key in ("artifact_paths", "output_directories"):
                values = dep.get(key)
                if isinstance(values, list):
                    raw_paths.extend(values)
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
        tool_name: str = "code_executor",
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
            f"- Continue {task_label} now with real execution via {tool_name}.",
        ]
        if artifact_paths:
            lines.extend(
                [
                    "- Authoritative upstream deliverables are already available; do not stop with BLOCKED_DEPENDENCY before attempting execution.",
                    "- Use the upstream artifact paths/directories below directly instead of browsing more directories or documents.",
                    "- Produce only the current task's outputs.",
                    "",
                    "Authoritative upstream artifact paths/directories:",
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
        """Extract a tool name mentioned in the task instruction.

        If the instruction explicitly says "使用 literature_pipeline" or
        "use web_search", return that tool name so the forced followthrough
        uses the right tool instead of defaulting to code_executor.
        """
        if not instruction:
            return None
        lowered = instruction.lower()
        # Check each candidate tool — return the first one mentioned
        for tool in DeepThinkAgent._FOLLOWTHROUGH_TOOL_CANDIDATES:
            if tool in lowered:
                return tool
        # Also check Chinese tool references
        _TOOL_ALIASES = {
            "文献检索": "literature_pipeline",
            "文献搜索": "literature_pipeline",
            "搜索文献": "web_search",
            "网络搜索": "web_search",
            "序列获取": "sequence_fetch",
            "下载链接": "url_fetch",
            "下载文件": "url_fetch",
            "生物工具": "bio_tools",
        }
        for alias, tool in _TOOL_ALIASES.items():
            if alias in instruction:
                return tool
        return None

    async def _execute_forced_probe_followthrough(
        self,
        *,
        task_context: Optional[TaskExecutionContext],
        user_query: str,
        iteration: int,
        probe_only_execution_cycles: int,
    ) -> Dict[str, Any]:
        # Determine the best tool based on task instruction
        task_instruction = ""
        if task_context is not None and task_context.task_instruction:
            task_instruction = str(task_context.task_instruction).strip()

        recommended_tool = self._extract_recommended_tool_from_instruction(task_instruction)
        tool_name = recommended_tool or "code_executor"

        forced_task = self._build_forced_probe_followthrough_task(
            task_context=task_context,
            user_query=user_query,
            tool_name=tool_name,
        )
        logger.warning(
            "[DEEP_THINK_NATIVE] Forcing %s after probe-only cycles=%s task_id=%s (recommended=%s)",
            tool_name,
            probe_only_execution_cycles,
            getattr(task_context, "task_id", None),
            recommended_tool or "default",
        )

        # Build tool-specific arguments
        if tool_name == "code_executor":
            arguments = {"task": forced_task}
        elif tool_name == "literature_pipeline":
            arguments = {"query": task_instruction[:500], "session_id": getattr(self, "_session_id", None)}
        elif tool_name == "web_search":
            arguments = {"query": task_instruction[:200]}
        elif tool_name == "manuscript_writer":
            arguments = {"task": forced_task}
        else:
            # Generic: pass the task description
            arguments = {"task": forced_task}

        forced_call = SimpleNamespace(
            name=tool_name,
            id=f"forced_probe_followthrough_{iteration}_{probe_only_execution_cycles}",
            arguments=arguments,
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
        # Determine the best tool based on the next task's instruction
        task_instruction = ""
        if task_context is not None and task_context.task_instruction:
            task_instruction = str(task_context.task_instruction).strip()
        recommended_tool = self._extract_recommended_tool_from_instruction(task_instruction)
        tool_name = recommended_tool or "code_executor"

        forced_task = self._build_forced_handoff_followthrough_task(
            task_context=task_context,
            user_query=user_query,
            previous_task_id=previous_task_id,
            next_task_id=next_task_id,
        )
        logger.warning(
            "[DEEP_THINK_NATIVE] Forcing %s after task handoff previous=%s next=%s reason=%s iteration=%s",
            tool_name,
            previous_task_id,
            next_task_id,
            reason,
            iteration,
        )

        if tool_name == "code_executor":
            arguments = {"task": forced_task}
        elif tool_name == "literature_pipeline":
            arguments = {"query": task_instruction[:500], "session_id": getattr(self, "_session_id", None)}
        elif tool_name == "web_search":
            arguments = {"query": task_instruction[:200]}
        else:
            arguments = {"task": forced_task}

        forced_call = SimpleNamespace(
            name=tool_name,
            id=f"forced_handoff_followthrough_{iteration}_{next_task_id}_{reason}",
            arguments=arguments,
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

                output_location = candidate.get("output_location")
                base_dir_value = (
                    (output_location.get("base_dir") if isinstance(output_location, dict) else None)
                    or candidate.get("task_directory_full")
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

    @classmethod
    def _collect_evidence_scope_signals(cls, steps: Sequence[ThinkingStep]) -> List[Dict[str, Any]]:
        signals: List[Dict[str, Any]] = []
        for step in steps:
            for entry in cls._extract_tool_payloads_from_step(step):
                tool_name = str(entry.get("tool") or "").strip().lower()
                payload = entry.get("payload")
                if not isinstance(payload, dict):
                    continue
                inner = cls._unwrap_tool_result(payload)
                if not isinstance(inner, dict):
                    continue
                evidence_scope = inner.get("evidence_scope")
                if not isinstance(evidence_scope, dict):
                    evidence_scope = payload.get("evidence_scope")
                status_counts = None
                if isinstance(evidence_scope, dict):
                    status_counts = evidence_scope.get("status_counts")
                if status_counts is None and isinstance(inner.get("counts"), dict):
                    counts = inner.get("counts")
                    if any(key in counts for key in ("completed", "failed", "status_file_total")):
                        status_counts = {
                            key: counts.get(key)
                            for key in ("completed", "failed", "status_file_total")
                            if key in counts
                        }
                status_count_sources = None
                if isinstance(inner.get("status_count_sources"), list):
                    status_count_sources = inner.get("status_count_sources")
                elif isinstance(evidence_scope, dict) and isinstance(evidence_scope.get("status_count_sources"), list):
                    status_count_sources = evidence_scope.get("status_count_sources")
                status_counts_confidence = inner.get("status_counts_confidence")
                if status_counts_confidence is None and isinstance(evidence_scope, dict):
                    status_counts_confidence = evidence_scope.get("status_counts_confidence")
                signal: Dict[str, Any] = {
                    "tool": tool_name,
                    "operation": str(inner.get("operation") or payload.get("operation") or "").strip().lower(),
                    "path": inner.get("path") or payload.get("path"),
                    "counts": inner.get("counts") if isinstance(inner.get("counts"), dict) else None,
                    "summary": inner.get("summary") if isinstance(inner.get("summary"), str) else None,
                    "sample_items": inner.get("sample_items") if isinstance(inner.get("sample_items"), list) else None,
                    "evidence_scope": evidence_scope if isinstance(evidence_scope, dict) else None,
                    "reconciliation": inner.get("reconciliation") if isinstance(inner.get("reconciliation"), dict) else (
                        evidence_scope.get("reconciliation") if isinstance(evidence_scope, dict) and isinstance(evidence_scope.get("reconciliation"), dict) else None
                    ),
                    "completeness_status": inner.get("completeness_status") or payload.get("completeness_status"),
                    "status_counts": status_counts if isinstance(status_counts, dict) else None,
                    "status_count_sources": status_count_sources,
                    "status_counts_confidence": status_counts_confidence,
                    "incomplete_examples": inner.get("incomplete_examples") if isinstance(inner.get("incomplete_examples"), list) else None,
                    "partial_completion_suspected": bool(
                        inner.get("partial_completion_suspected")
                        or payload.get("partial_completion_suspected")
                    ),
                    "partial_ratio": inner.get("partial_ratio") or payload.get("partial_ratio"),
                }
                if signal["evidence_scope"] or signal["status_counts"] or signal["reconciliation"] or signal["incomplete_examples"] or signal["partial_completion_suspected"]:
                    signals.append(signal)
        return signals

    def _build_evidence_scope_notice(
        self,
        *,
        user_query: str,
        signals: Sequence[Dict[str, Any]],
        replace_claim: bool = False,
    ) -> str:
        completed: Optional[int] = None
        failed: Optional[int] = None
        failure_examples: List[str] = []
        partial_ratio = ""
        sampled_or_partial = False
        paths: List[str] = []
        profile_summaries: List[str] = []
        status_source_names: List[str] = []
        status_directory_names: List[str] = []
        reconciliation_notes: List[str] = []
        reconciliation_missing_examples: List[str] = []
        reconciliation_guidance: List[str] = []
        suffix_profile_text = ""
        success_structure_text = ""
        sampled_structure_notes: List[str] = []
        seen_sources: set[tuple[str, str, str]] = set()
        seen_profiles: set[str] = set()
        seen_status_names: set[str] = set()
        seen_reconciliation_notes: set[str] = set()
        seen_missing_examples: set[str] = set()
        seen_reconciliation_guidance: set[str] = set()
        seen_sampled_structure_notes: set[str] = set()

        for signal in signals:
            path = str(signal.get("path") or "").strip()
            if path and path not in paths:
                paths.append(path)
            counts = signal.get("counts") if isinstance(signal.get("counts"), dict) else {}
            if counts:
                profile_key = path or str(signal.get("operation") or "profile")
                if profile_key not in seen_profiles:
                    seen_profiles.add(profile_key)
                    metric_parts: List[str] = []
                    for key, label in (
                        ("direct_children", "direct_children"),
                        ("directories", "directories"),
                        ("sample_candidate_directories", "sample_candidate_directories"),
                        ("status_directories", "status_directories"),
                        ("files", "files"),
                        ("other", "other"),
                    ):
                        value = counts.get(key)
                        if isinstance(value, int):
                            metric_parts.append(f"{label}={value}")
                    if metric_parts:
                        profile_summaries.append(", ".join(metric_parts))
            evidence_scope = signal.get("evidence_scope") if isinstance(signal.get("evidence_scope"), dict) else {}
            directory_classification = (
                evidence_scope.get("directory_classification")
                if isinstance(evidence_scope.get("directory_classification"), dict)
                else {}
            )
            if directory_classification:
                for name in directory_classification.get("status_directory_names") or []:
                    name_text = str(name or "").strip()
                    if name_text and name_text not in status_directory_names:
                        status_directory_names.append(name_text)
            completeness = str(
                signal.get("completeness_status")
                or evidence_scope.get("completeness_status")
                or ""
            ).strip().lower()
            enumeration = evidence_scope.get("enumeration") if isinstance(evidence_scope.get("enumeration"), dict) else {}
            omitted = enumeration.get("omitted_children")
            if completeness in {"partial", "unknown"} or (isinstance(omitted, int) and omitted > 0):
                sampled_or_partial = True
            status_counts = signal.get("status_counts") if isinstance(signal.get("status_counts"), dict) else {}
            status_total = status_counts.get("status_file_total")
            sample_candidates = directory_classification.get("sample_candidate_directories")
            if isinstance(status_total, int) and isinstance(sample_candidates, int) and status_total != sample_candidates:
                note = f"status_file_total={status_total} differs from sample_candidate_directories={sample_candidates}"
                if note not in seen_reconciliation_notes:
                    seen_reconciliation_notes.add(note)
                    reconciliation_notes.append(note)
            reconciliation = signal.get("reconciliation") if isinstance(signal.get("reconciliation"), dict) else {}
            if reconciliation:
                rec_counts = reconciliation.get("counts") if isinstance(reconciliation.get("counts"), dict) else {}
                rec_examples = reconciliation.get("examples") if isinstance(reconciliation.get("examples"), dict) else {}
                status_missing = rec_counts.get("status_entries_missing_directories")
                failure_missing = rec_counts.get("failure_missing_directories")
                success_missing = rec_counts.get("success_missing_directories")
                sample_without_status = rec_counts.get("sample_dirs_without_status")
                rec_note_parts: List[str] = []
                for key, label in (
                    ("status_unique_total", "status_unique_total"),
                    ("sample_candidate_directories", "sample_candidate_directories"),
                    ("status_entries_missing_directories", "status_entries_missing_directories"),
                    ("failure_missing_directories", "failure_missing_directories"),
                    ("success_missing_directories", "success_missing_directories"),
                    ("sample_dirs_without_status", "sample_dirs_without_status"),
                    ("duplicate_success_entries", "duplicate_success_entries"),
                    ("duplicate_failure_entries", "duplicate_failure_entries"),
                    ("success_failure_overlap", "success_failure_overlap"),
                ):
                    value = rec_counts.get(key)
                    if isinstance(value, int):
                        rec_note_parts.append(f"{label}={value}")
                if rec_note_parts:
                    note = ", ".join(rec_note_parts)
                    if note not in seen_reconciliation_notes:
                        seen_reconciliation_notes.add(note)
                        reconciliation_notes.append(note)
                for key in ("failure_missing_directories", "success_missing_directories", "sample_dirs_without_status", "success_failure_overlap"):
                    values = rec_examples.get(key)
                    if not isinstance(values, list) or not values:
                        continue
                    for value in values[:5]:
                        text_value = str(value or "").strip()
                        if text_value and text_value not in seen_missing_examples:
                            seen_missing_examples.add(text_value)
                            reconciliation_missing_examples.append(text_value)
                for item in reconciliation.get("claim_guidance") or []:
                    guidance = str(item or "").strip()
                    if guidance and guidance not in seen_reconciliation_guidance:
                        seen_reconciliation_guidance.add(guidance)
                        reconciliation_guidance.append(guidance)
                if isinstance(status_missing, int) and status_missing and not (rec_counts.get("duplicate_success_entries") or rec_counts.get("duplicate_failure_entries") or rec_counts.get("success_failure_overlap")):
                    guidance = "Status/directory mismatch is explained by status IDs without matching directories; do not infer retries/reruns from this evidence."
                    if guidance not in seen_reconciliation_guidance:
                        seen_reconciliation_guidance.add(guidance)
                        reconciliation_guidance.append(guidance)
                name_profile = reconciliation.get("sample_directory_name_profile") if isinstance(reconciliation.get("sample_directory_name_profile"), dict) else {}
                suffix_counts = name_profile.get("hyphen_suffix_counts") if isinstance(name_profile.get("hyphen_suffix_counts"), dict) else {}
                if suffix_counts and not suffix_profile_text:
                    suffix_profile_text = ", ".join(f"{key}={value}" for key, value in list(suffix_counts.items())[:12])
                structure = reconciliation.get("success_directory_structure") if isinstance(reconciliation.get("success_directory_structure"), dict) else {}
                file_distribution = structure.get("file_count_distribution") if isinstance(structure.get("file_count_distribution"), dict) else {}
                if file_distribution and not success_structure_text:
                    scanned = structure.get("directories_scanned")
                    considered = structure.get("entries_considered")
                    patterns = structure.get("common_file_patterns") if isinstance(structure.get("common_file_patterns"), list) else []
                    pattern_text = ", ".join(
                        str(item.get("pattern"))
                        for item in patterns[:5]
                        if isinstance(item, dict) and item.get("pattern")
                    )
                    success_structure_text = (
                        f"success directories scanned={scanned}/{considered}, file_count_distribution={file_distribution}"
                        + (f", common_file_patterns={pattern_text}" if pattern_text else "")
                    )
            count_sources = signal.get("status_count_sources") if isinstance(signal.get("status_count_sources"), list) else []
            if count_sources:
                for source in count_sources:
                    if not isinstance(source, dict):
                        continue
                    if source.get("count_confidence") != "high":
                        continue
                    source_path = str(source.get("path") or source.get("name") or path).strip()
                    kind = str(source.get("kind") or "").strip()
                    key = (source_path, kind, str(source.get("count_source") or ""))
                    if key in seen_sources:
                        continue
                    seen_sources.add(key)
                    if source_path and source_path not in seen_status_names:
                        seen_status_names.add(source_path)
                        status_source_names.append(str(source.get("name") or source_path).strip())
                    entry_count = source.get("entry_count")
                    if not isinstance(entry_count, int):
                        continue
                    if kind == "success":
                        completed = (completed or 0) + entry_count
                    elif kind == "failure":
                        failed = (failed or 0) + entry_count
            else:
                fallback_key = (path, str(signal.get("operation") or ""), "status_counts")
                if fallback_key not in seen_sources:
                    seen_sources.add(fallback_key)
                    if isinstance(status_counts.get("completed"), int):
                        completed = (completed or 0) + int(status_counts["completed"])
                    if isinstance(status_counts.get("failed"), int):
                        failed = (failed or 0) + int(status_counts["failed"])
            examples = signal.get("incomplete_examples")
            if isinstance(examples, list):
                for item in examples[:5]:
                    if isinstance(item, dict):
                        name = str(item.get("name") or "").strip()
                        reason = str(item.get("reason") or "").strip()
                        if name:
                            failure_examples.append(f"{name} ({reason})" if reason else name)
            if signal.get("partial_completion_suspected"):
                sampled_or_partial = True
                if signal.get("partial_ratio"):
                    partial_ratio = str(signal.get("partial_ratio"))
            sample_items = signal.get("sample_items") if isinstance(signal.get("sample_items"), list) else []
            sampled_dirs = [
                item
                for item in sample_items
                if isinstance(item, dict)
                and str(item.get("type") or "").strip().lower() == "directory"
                and item.get("child_count") is not None
            ]
            if sampled_dirs:
                structure_counts = sorted(
                    {
                        int(item.get("child_count"))
                        for item in sampled_dirs
                        if isinstance(item.get("child_count"), int)
                    }
                )
                note = (
                    f"per-sample file structure is based on {len(sampled_dirs)} sampled direct child directories"
                    + (f" with observed child_count values {structure_counts[:5]}" if structure_counts else "")
                )
                if note not in seen_sampled_structure_notes:
                    seen_sampled_structure_notes.add(note)
                    sampled_structure_notes.append(note)

        path_text = ", ".join(paths[:3]) if paths else "the inspected path"
        completed_text = completed if completed is not None else "unknown"
        failed_text = failed if failed is not None else "unknown"
        examples_text = ", ".join(failure_examples[:5])
        profile_text = "; ".join(profile_summaries[:3])
        source_text = ", ".join(status_source_names[:5])
        status_dirs_text = ", ".join(status_directory_names[:5])
        reconciliation_text = "; ".join(reconciliation_notes[:3])
        reconciliation_examples_text = ", ".join(reconciliation_missing_examples[:8])
        reconciliation_guidance_text = " ".join(reconciliation_guidance[:3])
        sampled_structure_text = "; ".join(sampled_structure_notes[:3])
        label = "Corrected evidence-scoped conclusion" if replace_claim else "Evidence-scope note"
        if replace_claim:
            lines = [f"{label}:"]
            lines.append(f"- Evidence scope: tool output for {path_text}.")
            if profile_text:
                lines.append(f"- Directory profile: {profile_text}.")
            if completed is not None or failed is not None:
                lines.append(f"- Status counts: completed={completed_text}, failed={failed_text}.")
            if source_text:
                lines.append(f"- Count sources: {source_text}.")
            if status_dirs_text:
                lines.append(f"- Status/progress directories: {status_dirs_text}. Do not report root direct_children as verified sample-directory count.")
            if reconciliation_text:
                lines.append(f"- Reconciliation needed: {reconciliation_text}.")
            if reconciliation_examples_text:
                lines.append(f"- Reconciliation examples: {reconciliation_examples_text}.")
            if reconciliation_guidance_text:
                lines.append(f"- Reconciliation interpretation limit: {reconciliation_guidance_text}")
            if suffix_profile_text:
                lines.append(f"- Sample-name suffix distribution: {suffix_profile_text}.")
            if success_structure_text:
                lines.append(f"- Completed-directory file structure: {success_structure_text}.")
            if sampled_structure_text:
                lines.append(f"- Per-sample file structure evidence: {sampled_structure_text}; do not state that each/all samples share that structure.")
            if partial_ratio:
                lines.append(f"- Partial-completion signal: {partial_ratio}.")
            if examples_text:
                lines.append(f"- Failure/incomplete examples: {examples_text}.")
            if sampled_or_partial:
                lines.append("- Scope caution: Do not treat sampled or compacted listings as evidence that all samples succeeded.")
            lines.append("- Correction: the original all-success/global completion claim is not supported by the available evidence.")
            return "\n".join(lines)

        parts = [f"{label}: this run is limited to tool output for {path_text}. "]
        if profile_text:
            parts.append(f"Directory profile: {profile_text}. ")
        if completed is not None or failed is not None:
            parts.append(f"Observed status counts: completed={completed_text}, failed={failed_text}. ")
        if status_dirs_text:
            parts.append(f"Status/progress directories: {status_dirs_text}; root direct_children is not a verified sample-directory count. ")
        if reconciliation_text:
            parts.append(f"Reconciliation needed: {reconciliation_text}. ")
        if reconciliation_examples_text:
            parts.append(f"Reconciliation examples: {reconciliation_examples_text}. ")
        if reconciliation_guidance_text:
            parts.append(f"Reconciliation interpretation limit: {reconciliation_guidance_text} ")
        if suffix_profile_text:
            parts.append(f"Sample-name suffix distribution: {suffix_profile_text}. ")
        if success_structure_text:
            parts.append(f"Completed-directory file structure: {success_structure_text}. ")
        if sampled_structure_text:
            parts.append(f"Per-sample file structure evidence: {sampled_structure_text}; do not state that each/all samples share that structure. ")
        if partial_ratio:
            parts.append(f"A partial-completion signal was detected: {partial_ratio}. ")
        if examples_text:
            parts.append(f"Failure/incomplete examples: {examples_text}. ")
        if sampled_or_partial:
            parts.append("Do not treat sampled or compacted listings as evidence that all samples succeeded.")
        if replace_claim:
            parts.append(" The original global success claim is not supported by the available evidence.")
        return "".join(parts).strip()

    def _apply_evidence_scope_truth_barrier(
        self,
        answer: str,
        *,
        user_query: str,
        steps: Sequence[ThinkingStep],
    ) -> str:
        text = str(answer or "").strip()
        if not text:
            return text
        signals = self._collect_evidence_scope_signals(steps)
        if not signals:
            return text
        if _answer_acknowledges_failed_status_counts(text, signals):
            return text

        needs_notice = False
        replace_claim = False
        has_global_success_claim = _looks_like_global_success_claim_text(text)
        has_unverified_sample_directory_claim = bool(_UNVERIFIED_SAMPLE_DIRECTORY_CLAIM_RE.search(text))
        has_unverified_each_file_structure_claim = bool(_UNVERIFIED_EACH_FILE_STRUCTURE_RE.search(text))
        has_unsupported_retry_rerun_claim = bool(_UNSUPPORTED_RETRY_RERUN_RE.search(text))
        for signal in signals:
            evidence_scope = signal.get("evidence_scope") if isinstance(signal.get("evidence_scope"), dict) else {}
            enumeration = evidence_scope.get("enumeration") if isinstance(evidence_scope.get("enumeration"), dict) else {}
            omitted = enumeration.get("omitted_children")
            status_counts = signal.get("status_counts") if isinstance(signal.get("status_counts"), dict) else {}
            counts = signal.get("counts") if isinstance(signal.get("counts"), dict) else {}
            directory_classification = (
                evidence_scope.get("directory_classification")
                if isinstance(evidence_scope.get("directory_classification"), dict)
                else {}
            )
            if has_unverified_sample_directory_claim and (
                counts.get("status_directories")
                or directory_classification.get("status_directories")
                or directory_classification.get("sample_candidate_directories") != counts.get("direct_children")
            ):
                needs_notice = True
                replace_claim = True
            if has_unverified_each_file_structure_claim:
                sample_items = signal.get("sample_items") if isinstance(signal.get("sample_items"), list) else []
                reconciliation = signal.get("reconciliation") if isinstance(signal.get("reconciliation"), dict) else {}
                success_structure = reconciliation.get("success_directory_structure") if isinstance(reconciliation.get("success_directory_structure"), dict) else {}
                file_distribution = success_structure.get("file_count_distribution") if isinstance(success_structure.get("file_count_distribution"), dict) else {}
                complete_structure_scan = bool(success_structure.get("complete_scan"))
                if not (complete_structure_scan and len(file_distribution) == 1):
                    if sample_items:
                        needs_notice = True
                        replace_claim = True
            if has_unsupported_retry_rerun_claim:
                reconciliation = signal.get("reconciliation") if isinstance(signal.get("reconciliation"), dict) else {}
                rec_counts = reconciliation.get("counts") if isinstance(reconciliation.get("counts"), dict) else {}
                has_retry_evidence = bool(
                    rec_counts.get("duplicate_success_entries")
                    or rec_counts.get("duplicate_failure_entries")
                    or rec_counts.get("success_failure_overlap")
                )
                if reconciliation and not has_retry_evidence:
                    needs_notice = True
                    replace_claim = True
            if isinstance(status_counts.get("failed"), int) and status_counts.get("failed", 0) > 0:
                needs_notice = True
                if has_global_success_claim:
                    replace_claim = True
            if signal.get("partial_completion_suspected"):
                needs_notice = True
                if has_global_success_claim:
                    replace_claim = True
            if isinstance(omitted, int) and omitted > 0 and has_global_success_claim:
                needs_notice = True
                replace_claim = True
            completeness = str(
                signal.get("completeness_status")
                or evidence_scope.get("completeness_status")
                or ""
            ).strip().lower()
            if completeness in {"partial", "unknown"} and has_global_success_claim:
                needs_notice = True
                replace_claim = True
        if not needs_notice:
            return text

        notice = sanitize_professional_response_text(
            self._build_evidence_scope_notice(
                user_query=user_query,
                signals=signals,
                replace_claim=replace_claim,
            )
        )
        if not notice or text.startswith(notice):
            return text
        if replace_claim:
            return notice
        return f"{notice}\n\n{text}"

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
                        "already_bound_plan_reused": bool(
                            rp.get("already_bound_plan_reused")
                            or payload.get("already_bound_plan_reused")
                        ),
                    }
                )
        return events

    def _summarize_structured_plan_outcome(
        self,
        steps: List[ThinkingStep],
        *,
        user_query: str = "",
    ) -> Dict[str, Any]:
        # Enforce explicit plan lifecycle contracts. The LLM still decides how
        # to decompose and what evidence to gather, but once routing identifies
        # create/review/optimize/execute intent, prose-only answers are not
        # allowed to masquerade as real plan operations.
        plan_id = self._current_plan_id()
        plan_title = self._current_plan_title()
        flags = self._plan_contract_flags()
        route_reasons = self.request_profile.get("route_reason_codes")
        if not isinstance(route_reasons, list):
            route_reasons = []

        events = self._collect_plan_operation_events(steps)

        if flags["conflict_requires_confirmation"]:
            return {
                "required": True,
                "mode": "plan_conflict_confirmation",
                "called": bool(events),
                "satisfied": False,
                "state": "confirmation_required",
                "message": self._build_plan_conflict_confirmation_message(),
                "plan_id": plan_id,
                "plan_title": plan_title,
                "operation": None,
            }

        required_ops: List[str] = []
        if flags["create_required"]:
            required_ops.append("create")
        if flags["execute_required"]:
            required_ops.append("execute_all")
        if flags["review_required"]:
            required_ops.append("review")
        if flags["optimize_required"]:
            required_ops.append("optimize")

        if required_ops:
            def _successful_event(op_name: str) -> Optional[Dict[str, Any]]:
                for event in events:
                    if not event.get("success"):
                        continue
                    if event.get("operation") != op_name:
                        continue
                    if op_name == "create" and event.get("already_bound_plan_reused"):
                        continue
                    if op_name == "optimize" and event.get("applied_changes") == 0:
                        continue
                    return event
                return None

            create_event = _successful_event("create") if flags["create_required"] else None
            execute_event = _successful_event("execute_all") if flags["execute_required"] else None
            review_event = _successful_event("review") if flags["review_required"] else None
            optimize_event = _successful_event("optimize") if flags["optimize_required"] else None

            satisfied_ops: List[str] = []
            missing_ops: List[str] = []
            if flags["create_required"]:
                (satisfied_ops if create_event else missing_ops).append("create")
            if flags["execute_required"]:
                execute_matches_created = True
                if flags["execute_after_create_required"] and create_event and execute_event:
                    created_id = create_event.get("plan_id")
                    executed_id = execute_event.get("plan_id")
                    execute_matches_created = bool(created_id and executed_id == created_id)
                if execute_event and execute_matches_created:
                    satisfied_ops.append("execute_all")
                else:
                    missing_ops.append("execute_all")
            if flags["review_required"]:
                (satisfied_ops if review_event else missing_ops).append("review")
            if flags["optimize_required"]:
                (satisfied_ops if optimize_event else missing_ops).append("optimize")

            satisfied = not missing_ops
            last_op = events[-1].get("operation") if events else None
            outcome_plan_id = None
            for event in reversed(events):
                if event.get("plan_id") is not None:
                    outcome_plan_id = event.get("plan_id")
                    break
            if outcome_plan_id is None:
                outcome_plan_id = plan_id
            outcome_plan_title = plan_title
            for event in reversed(events):
                if event.get("plan_title"):
                    outcome_plan_title = event.get("plan_title")
                    break
            message = None
            if not satisfied:
                if events:
                    missing_text = ", ".join(missing_ops)
                    message = (
                        "The requested structured plan contract was not satisfied: "
                        f"missing successful plan_operation operation(s): {missing_text}."
                    )
                else:
                    missing_text = ", ".join(missing_ops)
                    message = (
                        "The requested structured plan contract was not satisfied: "
                        "plan_operation was not called. "
                        f"Required operation(s): {missing_text}."
                    )
            return {
                "required": True,
                "mode": "plan_lifecycle",
                "called": bool(events),
                "satisfied": satisfied,
                "state": "satisfied" if satisfied else ("called_but_incomplete" if events else "not_called"),
                "message": message,
                "plan_id": outcome_plan_id,
                "plan_title": outcome_plan_title,
                "operation": last_op,
                "required_operations": required_ops,
                "satisfied_operations": satisfied_ops,
                "missing_operations": missing_ops,
            }

        is_bound_plan_mutation_request = (
            plan_id is not None
            and any(
                code in route_reasons
                for code in ("plan_review", "plan_optimize")
            )
        )

        if not is_bound_plan_mutation_request:
            return {
                "required": False,
                "mode": None,
                "called": False,
                "satisfied": False,
                "state": None,
                "message": None,
                "plan_id": plan_id,
                "plan_title": plan_title,
                "operation": None,
            }

        # Check if plan_operation was actually called with a mutation operation.
        # Exclude "create" — when a plan is already bound, the tool wrapper
        # rewrites create into a no-op already_bound_plan_reused result that
        # does not actually modify the plan.
        mutation_ops = {"review", "optimize", "update"}
        called = bool(events)
        satisfied = any(
            e.get("success")
            and e.get("operation") in mutation_ops
            and not e.get("already_bound_plan_reused")
            for e in events
        )
        last_op = events[-1].get("operation") if events else None

        return {
            "required": True,
            "mode": "bound_plan_mutation",
            "called": called,
            "satisfied": satisfied,
            "state": "satisfied" if satisfied else ("called_but_failed" if called else "not_called"),
            "message": None if satisfied else (
                "plan_operation was called but did not succeed"
                if called
                else "plan_operation was not called; the user requested a plan mutation"
            ),
            "plan_id": plan_id,
            "plan_title": plan_title,
            "operation": last_op,
        }

    def _build_structured_plan_requirement_block(self) -> str:
        return _prompts._build_structured_plan_requirement_block(self)

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

    def _build_structured_plan_contract_failure_answer(
        self,
        *,
        outcome: Dict[str, Any],
        user_query: str,
    ) -> str:
        if str(outcome.get("state") or "") == "confirmation_required":
            return self._build_plan_conflict_confirmation_message()
        message = sanitize_professional_response_text(str(outcome.get("message") or "").strip())
        if not message:
            missing = outcome.get("missing_operations")
            if isinstance(missing, list) and missing:
                message = (
                    "The requested structured plan contract was not satisfied: missing successful "
                    f"plan_operation operation(s): {', '.join(str(item) for item in missing)}."
                )
            else:
                message = "The requested structured plan contract was not satisfied."
        required = outcome.get("required_operations")
        if isinstance(required, list) and required:
            return (
                f"{message}\n\n"
                f"Required operation(s): {', '.join(str(item) for item in required)}. "
                "I cannot treat file probes, terminal checks, or ordinary markdown text as a completed structured plan operation."
            )
        return message

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
        text = str(user_query or "").strip()
        if not text:
            return False
        return bool(_DIRECTORY_DATASET_REQUEST_RE.search(text) and _ABSOLUTE_PATH_RE.search(text))

    @staticmethod
    def _extract_directory_path_from_query(user_query: str) -> Optional[str]:
        matches = [match.group(0).rstrip(".,;:)]}>") for match in _ABSOLUTE_PATH_RE.finditer(str(user_query or ""))]
        if not matches:
            return None
        return max(matches, key=len)

    @classmethod
    def _phagescope_dataset_analysis_requested(cls, user_query: str) -> bool:
        text = str(user_query or "").strip()
        if not text:
            return False
        path = cls._extract_directory_path_from_query(text) or ""
        path_mentions_phagescope = "phagescope" in path.lower()
        if not path_mentions_phagescope:
            return False
        if cls._path_is_generic_tabular_file(path):
            return False
        # A real on-disk directory without a meta_data/ child is NOT a PhageScope
        # dataset (e.g. .../phagescope/test holding a clinical xlsx). deep_profile
        # would fail with "Missing meta_data directory", so demote here and let the
        # generic tool chain (code_executor) handle it.
        if cls._directory_positively_lacks_phagescope_meta_data(path):
            return False
        return bool(
            _PHAGESCOPE_DATASET_REQUEST_RE.search(text)
            or _PHAGESCOPE_ANALYSIS_ACTION_RE.search(text)
        )

    @staticmethod
    def _path_is_generic_tabular_file(path: str) -> bool:
        text = str(path or "").strip().strip("`'\"").rstrip(".,;:)]}>，。；：）】》").lower()
        return text.endswith(_NON_PHAGESCOPE_TABULAR_FILE_EXTS)

    @staticmethod
    def _directory_positively_lacks_phagescope_meta_data(path: str) -> bool:
        text = str(path or "").strip().strip("`'\"").rstrip(".,;:)]}>，。；：）】》")
        if not text:
            return False
        try:
            return os.path.isdir(text) and not os.path.isdir(os.path.join(text, "meta_data"))
        except OSError:
            return False

    @staticmethod
    def _directory_payload_is_generic_tabular_only(path: str) -> bool:
        # Positive on-disk evidence that this is a simple tabular data folder (e.g. one
        # clinical .xlsx), not a multi-file dataset needing a profile/census: demote the
        # barrier so code_executor analyzes it directly. Conservative on purpose.
        text = str(path or "").strip().strip("`'\"").rstrip(".,;:)]}>，。；：）】》")
        if not text:
            return False
        try:
            if not os.path.isdir(text):
                return False
            saw_tabular_file = False
            with os.scandir(text) as entries:
                for entry in entries:
                    if entry.name.startswith("."):
                        continue
                    if entry.is_dir():
                        return False
                    if not entry.is_file():
                        continue
                    if entry.name.lower().endswith(_NON_PHAGESCOPE_TABULAR_FILE_EXTS):
                        saw_tabular_file = True
                    else:
                        return False
            return saw_tabular_file
        except OSError:
            return False

    @classmethod
    def _file_operation_profile_or_census_seen(cls, steps: Sequence[ThinkingStep]) -> bool:
        for step in steps:
            for entry in cls._extract_tool_payloads_from_step(step):
                if str(entry.get("tool") or "").strip().lower() != "file_operations":
                    continue
                payload = entry.get("payload")
                if not isinstance(payload, dict):
                    continue
                inner = cls._unwrap_tool_result(payload)
                operation = str(inner.get("operation") or payload.get("operation") or "").strip().lower()
                if operation in {"profile", "census"}:
                    return True
        return False

    @classmethod
    def _phagescope_deep_profile_seen(cls, steps: Sequence[ThinkingStep]) -> bool:
        for step in steps:
            for entry in cls._extract_tool_payloads_from_step(step):
                if str(entry.get("tool") or "").strip().lower() != "phagescope_research":
                    continue
                payload = entry.get("payload")
                if not isinstance(payload, dict):
                    continue
                inner = cls._unwrap_tool_result(payload)
                action = str(inner.get("action") or payload.get("action") or "").strip().lower()
                if action == "deep_profile" and inner.get("success", payload.get("success")) is not False:
                    return True
        return False

    @classmethod
    def _phagescope_deep_profile_failure(cls, steps: Sequence[ThinkingStep]) -> Optional[str]:
        for step in steps:
            for entry in cls._extract_tool_payloads_from_step(step):
                if str(entry.get("tool") or "").strip().lower() != "phagescope_research":
                    continue
                payload = entry.get("payload")
                if not isinstance(payload, dict):
                    continue
                inner = cls._unwrap_tool_result(payload)
                action = str(inner.get("action") or payload.get("action") or "").strip().lower()
                if action != "deep_profile":
                    continue
                success = inner.get("success", payload.get("success"))
                if success is not False:
                    continue
                error = inner.get("error") or payload.get("error") or inner.get("summary") or payload.get("summary")
                return str(error or "phagescope_research deep_profile failed").strip()
        return None

    def _build_phagescope_deep_profile_failure_answer(
        self,
        *,
        user_query: str,
        steps: Sequence[ThinkingStep],
    ) -> Optional[str]:
        if not self._phagescope_dataset_analysis_requested(user_query):
            return None
        if self._phagescope_deep_profile_seen(steps):
            return None
        error = self._phagescope_deep_profile_failure(steps)
        if not error:
            return None
        path = self._extract_directory_path_from_query(user_query) or "the PhageScope dataset path"
        return (
            f"I could not complete the PhageScope dataset analysis because `phagescope_research` "
            f"`deep_profile` failed for `{path}`: {error}. "
            "I am not going to synthesize a dataset-level answer from shallow file listings or sampled metadata. "
            "Please retry after fixing the tool/path permission issue; until then, any directory-listing evidence is only a limited diagnostic, not a complete PhageScope profile."
        )

    def _needs_phagescope_deep_profile_before_final(
        self,
        *,
        user_query: str,
        steps: Sequence[ThinkingStep],
    ) -> Optional[str]:
        if "phagescope_research" not in self.available_tools:
            return None
        if not self._phagescope_dataset_analysis_requested(user_query):
            return None
        if self._phagescope_deep_profile_seen(steps):
            return None
        return self._extract_directory_path_from_query(user_query)

    def _needs_directory_profile_before_final(
        self,
        *,
        user_query: str,
        steps: Sequence[ThinkingStep],
    ) -> Optional[str]:
        if "file_operations" not in self.available_tools:
            return None
        if (
            "phagescope_research" in self.available_tools
            and self._phagescope_dataset_analysis_requested(user_query)
        ):
            return None
        if not self._directory_dataset_analysis_requested(user_query):
            return None
        path = self._extract_directory_path_from_query(user_query)
        if path and self._directory_payload_is_generic_tabular_only(path):
            return None
        if self._file_operation_profile_or_census_seen(steps):
            return None
        return self._extract_directory_path_from_query(user_query)

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
        paths = self._extract_explicit_artifact_paths(result)
        text = str(result) if result is not None else ""
        if text:
            for m in self.ARTIFACT_PATH_RE.finditer(text):
                candidate = m.group(1)
                if not self._is_internal_artifact_path(candidate) and candidate not in paths:
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

    @staticmethod
    def _looks_like_artifact_path(path: str) -> bool:
        text = str(path or "").strip()
        if not text or "\n" in text or "\r" in text:
            return False
        if text.startswith(("http://", "https://")):
            return False
        if text.startswith(("/", "./", "../", "~")):
            return True
        if "/" in text or "\\" in text:
            return True
        return Path(text).suffix.lower() in _ARTIFACT_PATH_EXTS

    def _extract_explicit_artifact_paths(self, result: Any) -> List[str]:
        if not isinstance(result, dict):
            return []

        paths: List[str] = []

        def _append(value: Any) -> None:
            if not isinstance(value, str):
                return
            candidate = value.strip()
            if not self._looks_like_artifact_path(candidate):
                return
            if self._is_internal_artifact_path(candidate):
                return
            if candidate not in paths:
                paths.append(candidate)

        def _append_list(items: Any) -> None:
            if not isinstance(items, list):
                return
            for item in items:
                _append(item)

        for key in ("artifact_paths", "session_artifact_paths", "produced_files"):
            _append_list(result.get(key))

        for key in (
            "image_path",
            "output_file",
            "output_file_rel",
            "saved_path",
            "saved_path_rel",
            "preview_path",
            "summary_file",
            "summary_file_rel",
        ):
            _append(result.get(key))

        items = result.get("items")
        if isinstance(items, list):
            for row in items:
                if not isinstance(row, dict):
                    continue
                _append(row.get("path"))
                _append(row.get("relative_path"))

        outputs = result.get("outputs")
        if isinstance(outputs, dict):
            for value in outputs.values():
                _append(value)

        storage = result.get("storage")
        if isinstance(storage, dict):
            for container in (
                storage,
                storage.get("relative") if isinstance(storage.get("relative"), dict) else None,
            ):
                if not isinstance(container, dict):
                    continue
                for key in (
                    "preview_path",
                    "result_path",
                    "output_file",
                    "output_file_rel",
                    "saved_path",
                    "saved_path_rel",
                ):
                    _append(container.get(key))
                for key in ("artifact_paths", "paths"):
                    _append_list(container.get(key))

        deliverables = result.get("deliverables")
        if isinstance(deliverables, dict):
            artifacts = deliverables.get("artifacts")
            if isinstance(artifacts, list):
                for row in artifacts:
                    if not isinstance(row, dict):
                        continue
                    _append(row.get("path"))
                    _append(row.get("relative_path"))

        files_saved = result.get("files_saved")
        output_directory = result.get("output_directory")
        if isinstance(files_saved, dict):
            base_dir = None
            if isinstance(output_directory, str) and output_directory.strip():
                try:
                    base_dir = Path(output_directory).expanduser().resolve()
                except Exception:
                    base_dir = None
            for value in files_saved.values():
                if not isinstance(value, str):
                    continue
                if base_dir is not None:
                    try:
                        candidate = Path(value).expanduser()
                        if not candidate.is_absolute():
                            candidate = (base_dir / candidate).resolve()
                        else:
                            candidate = candidate.resolve()
                        _append(str(candidate))
                        continue
                    except Exception:
                        pass
                _append(value)

        return paths

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
            owner_id=str(self.request_profile.get("owner_id") or "").strip() or None,
            extra={
                "chat_history": list(self.messages[-20:]) if getattr(self, "messages", None) else [],
                "paper_mode": bool(self.request_profile.get("paper_mode", False)),
                "deep_think_enabled": True,
            },
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
                
                if tool_name == "plan_operation" and isinstance(tool_result, dict):
                    if tool_result.get("operation") == "bind" and tool_result.get("success"):
                        new_plan_id = tool_result.get("plan_id")
                        if new_plan_id is not None:
                            self.request_profile["current_plan_id"] = new_plan_id
                            logger.info(
                                "[DEEP_THINK] Updated request_profile current_plan_id to %s after successful bind",
                                new_plan_id
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
        if str(tool_name or "").strip().lower() == "phagescope_research":
            return cls._compact_phagescope_research_result_for_llm(result)
        if str(tool_name or "").strip().lower() == "code_executor":
            return cls._compact_code_executor_result_for_llm(result)
        return None

    @classmethod
    def _compact_code_executor_result_for_llm(
        cls, result: Any
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(result, dict):
            return None
        _MAX_STDOUT_CHARS = 3000
        stdout_raw = str(result.get("stdout") or "")
        stdout_text = (
            stdout_raw[:_MAX_STDOUT_CHARS] + "…[truncated]"
            if len(stdout_raw) > _MAX_STDOUT_CHARS
            else stdout_raw
        )
        stderr_raw = str(result.get("stderr") or "")
        stderr_text = (
            stderr_raw[:1000] + "…[truncated]"
            if len(stderr_raw) > 1000
            else stderr_raw
        )
        compact: Dict[str, Any] = {
            "tool": "code_executor",
            "success": bool(result.get("success", False)),
            "exit_code": result.get("exit_code", -1),
            "output_files": result.get("output_files", []),
            "output_location": result.get("output_location"),
            "stdout": stdout_text,
            "llm_compacted": True,
        }
        if stderr_text:
            compact["stderr"] = stderr_text
        for key in ("error", "error_category", "error_summary", "fix_guidance",
                     "execution_status", "verification_status", "failure_kind",
                     "result", "code_file", "produced_files_count"):
            val = result.get(key)
            if val is not None:
                compact[key] = val
        return compact

    @classmethod
    def _compact_phagescope_research_result_for_llm(
        cls, result: Any
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(result, dict):
            return None
        if str(result.get("action") or "").strip().lower() != "deep_profile":
            return None
        compact: Dict[str, Any] = {
            "tool": "phagescope_research",
            "action": "deep_profile",
            "success": bool(result.get("success", True)),
            "data_dir": result.get("data_dir"),
            "resolved_data_dir": result.get("resolved_data_dir"),
            "metadata_files": result.get("metadata_files"),
            "metadata_rows": result.get("metadata_rows"),
            "unique_phage_ids": result.get("unique_phage_ids"),
            "duplicate_phage_ids": result.get("duplicate_phage_ids"),
            "metadata_size_bytes": result.get("metadata_size_bytes"),
            "metadata_size_human": result.get("metadata_size_human"),
            "total_size_bytes": result.get("total_size_bytes"),
            "total_size_human": result.get("total_size_human"),
            "ml_metadata_table": result.get("ml_metadata_table"),
            "label_quality": result.get("label_quality"),
            "split_readiness": result.get("split_readiness"),
            "annotation_inventory": result.get("annotation_inventory"),
            "anomalies": result.get("anomalies"),
            "claim_guidance": result.get("claim_guidance"),
            "recommended_next_step": result.get("recommended_next_step"),
            "llm_compacted": True,
        }
        metadata_schema = result.get("metadata_schema")
        if isinstance(metadata_schema, dict):
            compact["metadata_schema"] = {
                "expected_columns": metadata_schema.get("expected_columns"),
                "headers_consistent": metadata_schema.get("headers_consistent"),
                "most_common_header": metadata_schema.get("most_common_header"),
                "missing_expected_by_file": metadata_schema.get("missing_expected_by_file"),
                "extra_columns_by_file": metadata_schema.get("extra_columns_by_file"),
            }
        rows_by_file = result.get("rows_by_metadata_file")
        if isinstance(rows_by_file, dict):
            compact["rows_by_metadata_file"] = rows_by_file
        source_top = result.get("source_top")
        if isinstance(source_top, list):
            compact["source_top"] = source_top[:10]
        taxonomy_top = result.get("taxonomy_top")
        if isinstance(taxonomy_top, list):
            compact["taxonomy_top"] = taxonomy_top[:10]
        subdir_summary = result.get("subdir_size_summary")
        if isinstance(subdir_summary, dict):
            compact["subdir_size_summary"] = {
                name: {
                    key: value
                    for key, value in summary.items()
                    if key in {"files", "size_bytes", "size_human"}
                }
                for name, summary in subdir_summary.items()
                if isinstance(summary, dict)
            }
        return compact

    @classmethod
    def _compact_file_operations_result_for_llm(
        cls, result: Any
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(result, dict):
            return None
        operation = str(result.get("operation") or "").strip().lower()
        if operation in {"profile", "census"}:
            compact: Dict[str, Any] = {
                "operation": operation,
                "path": str(result.get("path") or "").strip(),
                "success": bool(result.get("success", True)),
                "summary": result.get("summary"),
                "completeness_status": result.get("completeness_status"),
                "llm_compacted": True,
            }
            for key in ("counts", "extension_counts", "evidence_scope"):
                value = result.get(key)
                if isinstance(value, dict):
                    compact[key] = value
            for key in ("status_files", "status_count_sources", "incomplete_examples", "sample_items"):
                value = result.get(key)
                if isinstance(value, list) and value:
                    compact[key] = value[:20]
            reconciliation = result.get("reconciliation")
            if isinstance(reconciliation, dict):
                compact["reconciliation"] = reconciliation
            if result.get("status_counts_confidence") is not None:
                compact["status_counts_confidence"] = result.get("status_counts_confidence")
            return compact
        if operation != "list":
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
        evidence_scope = result.get("evidence_scope") if isinstance(result.get("evidence_scope"), dict) else None
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
            if evidence_scope:
                compact_result["evidence_scope"] = evidence_scope
                status_counts = evidence_scope.get("status_counts")
                if isinstance(status_counts, dict):
                    compact_result["status_counts"] = status_counts
                completeness_status = evidence_scope.get("completeness_status")
                if isinstance(completeness_status, str) and completeness_status:
                    compact_result["completeness_status"] = completeness_status
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
