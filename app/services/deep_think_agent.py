import asyncio
import hashlib
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

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
        self.available_tools = available_tools
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
        self.request_profile = dict(request_profile or {})
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

    @staticmethod
    def _is_exploratory_file_operation_call(tool_result: Dict[str, Any]) -> bool:
        if str(tool_result.get("tool_name") or "").strip().lower() != "file_operations":
            return False
        params = tool_result.get("tool_params")
        if not isinstance(params, dict):
            return False
        operation = str(params.get("operation") or "").strip().lower()
        return operation in _EXPLORATORY_FILE_OPERATIONS

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

        # Fallback: consult the declarative metadata dict (covers test
        # environments where register_all_tools() hasn't been called).
        from tool_box.tool_registry import _TOOL_METADATA
        meta = _TOOL_METADATA.get(tool_name)
        if meta is not None:
            return meta.get("is_read_only", False)

        return False

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
        if stage >= 2:
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
        floor = (self._capability_floor() or "plain_chat").strip().lower()
        non_plain = floor != "plain_chat"
        light_tool_note = (
            "\n- Capability is not plain_chat: use tools when the answer depends on file/workspace/remote state; "
            "a no-tool guess is not an acceptable substitute for a check you could run.\n"
            if non_plain
            else ""
        )
        std_tool_note = (
            "\n- Capability is not plain_chat: prioritize tool-backed facts over stylistic completeness.\n"
            if non_plain
            else ""
        )
        if tier == "light":
            return (
                "=== REQUEST TIER: LIGHT ===\n"
                "- Answer directly and briefly.\n"
                "- Use the smallest amount of explanation that fully answers the user.\n"
                "- Keep the tone professional and plain; avoid decorative emojis or hype.\n"
                "- Prefer finishing in one short reasoning pass.\n"
                + light_tool_note
            )
        if tier == "standard":
            return (
                "=== REQUEST TIER: STANDARD ===\n"
                "- Give a concise but complete direct answer.\n"
                "- Avoid research style output unless the user explicitly asks for sources or latest information.\n"
                "- Keep the tone professional and plain; avoid decorative emojis or hype.\n"
                "- Prefer low-overhead execution, but do not ignore required evidence.\n"
                + std_tool_note
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
                "- Do not silently rewrite the current task into an upstream preprocessing task just because prerequisite deliverables are missing.\n"
                "- For immutable source inputs, prefer canonical data-directory paths over same-named session-root `results/` copies, especially when the session copy is empty or malformed.\n"
                "- For single-cell integration tasks, fewer than 2 valid upstream samples means the preconditions are not met; do not claim integration succeeded.\n"
                + execute_focus_note
            )
        return ""

    def _build_capability_floor_block(self) -> str:
        capability_floor = self._capability_floor() or "plain_chat"
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
            "When CAPABILITY FLOOR is not plain_chat, prefer tool-backed checks for anything that depends on real files, remote services, or run results (unless the user clearly wants opinion-only).\n"
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
        had_real_execution_tool = False

        logger.info("[DEEP_THINK_NATIVE] Starting for: %s", user_query[:50])

        while iteration < self.max_iterations:
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

                if executable_calls:
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

                    if self._is_probe_only_execution_cycle(tool_results, task_context=task_context):
                        probe_only_execution_cycles += 1
                        if probe_only_execution_cycles >= 3:
                            # Hard stop for infinite observation loops — always active regardless of
                            # had_real_execution_tool. Without this, a post-execution AI that keeps
                            # reading non-existent files would silently burn through max_iterations.
                            current_step.status = "done"
                            if had_real_execution_tool:
                                # Task was executed; post-execution probing exceeded limit.
                                # Return whatever the AI last said rather than a BLOCKED answer.
                                current_step.self_correction = (
                                    "Stopped repeated post-execution observation-only probing."
                                )
                                if not final_answer:
                                    final_answer = self._build_blocked_dependency_answer(
                                        task_context=task_context,
                                        user_query=user_query,
                                        tool_results=tool_results,
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
                            logger.info(
                                "[DEEP_THINK_NATIVE] Skipping probe-only nudge/block: had_real_execution_tool=True, probe_cycles=%s iter=%s",
                                probe_only_execution_cycles,
                                iteration,
                            )
                    else:
                        probe_only_execution_cycles = 0
                        had_real_execution_tool = True

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
            if not final_answer and iteration >= self.max_iterations - 1:
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
            "3a. If capability_floor is not plain_chat and the user asks about files, data, or remote job state, prefer at least one relevant tool call before a final answer.\n"
            "4. Call submit_final_answer once the user's request is adequately answered.\n"
            "5. Keep iterative reasoning visible to the user, but concise and relevant.\n\n"
            + "=== AVAILABLE TOOLS ===\n"
            + "\n".join(f"- {tool}" for tool in self.available_tools)
            + "\n\n"
            + self._build_protocol_boundary_block("native")
            + "\n=== RULES ===\n"
            "- Do NOT call submit_final_answer prematurely.\n"
            "- Do NOT launch broad web/literature research unless the user asks for sources, latest information, deep analysis, or the task is clearly evidence-sensitive.\n"
            "- Prefer zero-tool or one-tool answers for simple requests when capability_floor is plain_chat; when it is not, prefer necessary tool verification over guessing.\n"
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
                normalized_payload = {
                    "success": callback_success,
                    "tool": tool_name,
                    "result": tool_result,
                    "error": callback_error,
                }
                tool_result_text = json.dumps(normalized_payload, ensure_ascii=False, default=str)
                evidence = self._extract_evidence(tool_name, tool_params, tool_result)
                return {
                    "index": index,
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "tool_params": tool_params,
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
            "code_executor": "Execute Python/shell code. FALLBACK TOOL: Use this ONLY when bio_tools cannot handle the task (e.g., custom analysis scripts, complex data processing). For FASTA/FASTQ sequence stats or standard bioinformatics tasks, ALWAYS try bio_tools first. Params: {\"task\": \"description\"}",
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
Analyzes CSV, TSV, MAT, NPY data files by generating and executing Python code via Claude Code.

Operations:
- metadata: Extract dataset metadata (columns, types, samples)
- generate: Generate Python analysis code based on task description
- execute: Execute Python code via Claude Code
- analyze: Full pipeline (metadata → generate → execute with auto-fix)

Params for analyze (recommended):
{"operation": "analyze", "file_paths": ["/path/to/data.csv"], "task_title": "Analysis Title", "task_description": "What to analyze"}

Params for metadata:
{"operation": "metadata", "file_path": "/path/to/data.csv"}

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
        floor = (self._capability_floor() or "plain_chat").strip().lower()
        if tier == "light":
            if floor != "plain_chat":
                return (
                    "Light tier but non-plain capability: if you still need file or tool evidence to answer "
                    "accurately, call the tool now; otherwise finish with submit_final_answer."
                )
            return 'This is a light request. Finish now unless another short step is strictly necessary.'
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

    def _build_structured_fallback(self, steps: List[ThinkingStep], user_query: str = "") -> str:
        """Last-resort fallback: include evidence excerpts rather than a generic 'no answer' message."""
        n = len(steps)
        counts = self._collect_tool_usage_counts(steps)
        stats = self._format_tool_usage_counts(counts)
        language = detect_reasoning_language(user_query)

        # Collect any meaningful evidence to include in the fallback
        evidence = self._collect_evidence_snippets(steps, max_steps=12, max_chars=4000, per_snippet_max=1200)
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
        if evidence.strip() and len(evidence.strip()) > 100:
            if language == "zh":
                tools_part = f"（{stats}）" if stats else ""
                header = f"经过 {n} 步深度思考{tools_part}，以下是收集到的关键信息：\n\n"
                footer = "\n\n---\n*以上为工具输出摘要，可能需要进一步验证。如需更精确的结果，建议缩小问题范围后重试。*"
            else:
                tools_part = f" ({stats})" if stats else ""
                header = f"After {n} reasoning step(s){tools_part}, here are the key findings:\n\n"
                footer = "\n\n---\n*The above is a summary of tool outputs and may need further verification. For more precise results, try narrowing the question.*"
            return header + evidence.strip() + footer

        if useful_thoughts:
            combined = "\n\n".join(useful_thoughts)
            if language == "zh":
                tools_part = f"（{stats}）" if stats else ""
                return f"经过 {n} 步深度思考{tools_part}，以下是分析过程中的关键发现：\n\n{combined}"
            tools_part = f" ({stats})" if stats else ""
            return f"After {n} reasoning step(s){tools_part}, here are the key insights from the analysis:\n\n{combined}"

        # Truly nothing useful — keep minimal message
        if language == "zh":
            tools_part = f"（{stats}）" if stats else "（未解析到明确工具名）"
            return (
                f"经过 {n} 步深度思考{tools_part}，暂未形成可直接提交的确定结论。"
                "这个问题可能需要更聚焦的范围，或需要进一步验证关键信息后再收敛。"
                "建议缩小问题范围，改成更具体、可核验的问题后重试。"
            )
        tools_part = f" ({stats})" if stats else ""
        return (
            f"DeepThink completed {n} reasoning step(s){tools_part} but did not reach a final answer. "
            "This request likely needs a narrower scope or an additional verification pass before it can be concluded. "
            "Try a more specific, fact-checkable follow-up."
        )

    async def _generate_fallback_from_evidence(
        self,
        user_query: str,
        evidence_snippets: str,
        steps: List[ThinkingStep],
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
                f"=== EVIDENCE FROM TOOLS ===\n{evidence}\n\n"
            )
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
                return await self._generate_fallback_from_evidence(uq, evidence, steps)
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
        if evidence.strip() and uq:
            try:
                return await self._generate_fallback_from_evidence(uq, evidence, steps)
            except Exception:
                logger.warning(
                    "DeepThink fallback synthesis from tool evidence failed; using structured fallback.",
                    exc_info=True,
                )
        return self._build_structured_fallback(steps, user_query)

    async def _generate_summary(self, steps: List[ThinkingStep], user_query: str) -> str:
        """Generate a concise DeepThink summary via LLM in strict mode."""
        if not steps:
            raise DeepThinkProtocolError(
                "DeepThink summary generation requires at least one thinking step."
            )

        if not hasattr(self.llm_client, "chat_async"):
            raise DeepThinkProtocolError(
                "LLM client does not support chat_async required for summary generation."
            )

        usage_counts = self._collect_tool_usage_counts(steps)
        tool_stats_line = self._format_tool_usage_counts(usage_counts)
        tools_used_unique = list(usage_counts.keys())
        language = detect_reasoning_language(user_query)

        steps_lines: List[str] = []
        for step in self._select_steps_for_summary(steps):
            visible = build_user_visible_step(step, language=language)
            display_text = str(visible.get("display_text") or "").strip()
            if not display_text:
                display_text = _localized_text(
                    language,
                    "处理当前步骤",
                    "Processing the current step",
                )
            steps_lines.append(f"- Step {step.iteration}: {display_text}")
        steps_text = "\n".join(steps_lines)

        prompt = (
            f"User question:\n{user_query[:200]}\n\n"
            "Write a concise thinking summary for the user in the same language as the user question.\n"
            "Requirements:\n"
            "- Use 1 short sentence for very simple flows, or at most 2 short sentences for tool-heavy flows.\n"
            "- Describe the visible progression, not hidden internal deliberation.\n"
            "- Do not use phrases like 'I should', 'the user is asking', or other internal self-talk.\n"
            "- Keep the tone product-like and easy to scan.\n"
            f"- {PROFESSIONAL_STYLE_INSTRUCTION}\n\n"
            "Visible steps:\n"
            f"{steps_text}\n"
            f"Tools used: {', '.join(sorted(tools_used_unique)) if tools_used_unique else 'None'}\n"
            f"Tool call counts: {tool_stats_line if tool_stats_line else 'None'}\n\n"
            "Summary:"
        )

        try:
            summary = await asyncio.wait_for(
                self.llm_client.chat_async(prompt=prompt, max_tokens=150),
                timeout=10,
            )
        except Exception as exc:
            raise DeepThinkProtocolError(
                "LLM summary generation failed in strict mode."
            ) from exc

        cleaned = sanitize_professional_response_text(str(summary or "").strip())
        if len(cleaned) <= 10:
            raise DeepThinkProtocolError(
                "LLM summary output is empty or too short in strict mode."
            )
        normalized = sanitize_reasoning_text(cleaned, language=language, max_chars=None) or cleaned
        return sanitize_professional_response_text(normalized)
