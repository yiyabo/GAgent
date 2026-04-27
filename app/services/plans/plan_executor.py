from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field, ValidationError

from ...config.executor_config import ExecutorSettings, get_executor_settings
from ...llm import LLMClient, NativeStreamResult
from ..tool_schemas import build_executor_tool_schemas, EXECUTOR_AVAILABLE_TOOLS
from ..deep_think_agent import (
    DeepThinkAgent,
    TaskExecutionContext,
    ThinkingStep,
    build_user_visible_step,
    detect_reasoning_language,
)
from ..deliverables import get_deliverable_publisher
from ..execution.tool_executor import ToolExecutionContext, UnifiedToolExecutor
from ..llm.llm_service import LLMService
from ..skills import get_skills_loader
from .artifact_contracts import (
    artifact_manifest_path,
    extend_contract_with_runtime_candidates,
    find_candidate_source_for_alias,
    find_runtime_candidates,
    infer_artifact_contract,
    load_artifact_manifest,
    producer_candidates_for_alias,
    publish_artifact,
    published_artifact_paths_for_task,
    resolve_artifact_contract_with_provenance,
    resolve_manifest_aliases,
    save_artifact_manifest,
)
from .artifact_preflight import ArtifactPreflightService
from .plan_models import PlanNode, PlanTree
from .status_resolver import PlanStatusResolver
from .task_verification import TaskVerificationService, VerificationFinalization

logger = logging.getLogger(__name__)


def _run_coroutine_sync(coro: Any) -> Any:
    """Run an async coroutine synchronously, handling nested event loops.

    When called from a thread that already has a running event loop (e.g. via
    asyncio.to_thread), this spawns a new thread with its own event loop to
    avoid "cannot run nested event loop" errors.

    Thread-safety note: httpx.AsyncClient is thread-safe per its documentation,
    so creating a new event loop in a worker thread to drive the same client
    instance is safe.  However, any non-thread-safe state (e.g. mutable shared
    caches) accessed inside ``coro`` must use appropriate synchronization.
    """
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if running_loop and running_loop.is_running():
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(lambda: asyncio.run(coro)).result()
    return asyncio.run(coro)


def _log_job(level: str, message: str, metadata: Optional[Dict[str, Any]] = None) -> None:
    try:
        from .decomposition_jobs import log_job_event
    except Exception:  # pragma: no cover - defensive
        return
    log_job_event(level, message, metadata)


def _summarize_tool_params(params: Optional[Dict[str, Any]], max_length: int = 200) -> str:
    """Create a brief summary of tool parameters for logging."""
    if not params:
        return "(no parameters)"
    summary = json.dumps(params, ensure_ascii=False)
    if len(summary) > max_length:
        return summary[:max_length] + "..."
    return summary


if TYPE_CHECKING:  # pragma: no cover
    from ...repository.plan_repository import PlanRepository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PATH_LIKE_RE = re.compile(r"(/[a-zA-Z0-9_./-]{8,})")
_INTERNAL_ARTIFACT_FILENAMES = {"result.json", "manifest.json", "preview.json"}
_INTERNAL_TOOL_OUTPUT_RE = re.compile(
    r"(?:^|/)tool_outputs/job_[^/]+/step_\d+_[^/]+(?:/.*)?$",
    re.IGNORECASE,
)
_NON_DELIVERABLE_WORKSPACE_RE = re.compile(
    r"/plan\d+_task\d+/run_[^/]+(?:/(?:results|code|data|docs))?$",
    re.IGNORECASE,
)
_LEGACY_SESSION_WORKSPACE_RE = re.compile(
    r"(?:^|/)runtime/session_current/workspace(?:/.*)?$",
    re.IGNORECASE,
)
_USELESS_RUNTIME_ROOT_RE = re.compile(r"(?:^|/)runtime/?$", re.IGNORECASE)


def _is_non_canonical_runtime_path(path: str) -> bool:
    normalized = "/" + str(path or "").strip().replace("\\", "/").lstrip("/")
    if not normalized or normalized == "/":
        return False
    return bool(
        _LEGACY_SESSION_WORKSPACE_RE.search(normalized)
        or _USELESS_RUNTIME_ROOT_RE.search(normalized)
    )


def _extract_paths_from_execution_result(raw: str, max_paths: int = 40) -> List[str]:
    """Extract absolute file paths from an execution result string."""
    if not raw:
        return []
    try:
        payload = json.loads(raw) if raw.strip().startswith("{") else {}
    except (json.JSONDecodeError, TypeError):
        payload = {}

    paths: List[str] = []
    seen: set = set()

    # Structured fields first
    for key in ("artifact_paths", "produced_files", "session_artifact_paths"):
        items = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(items, list):
            for p in items:
                text = str(p).strip()
                if (
                    text.startswith("/")
                    and not _is_non_canonical_runtime_path(text)
                    and text not in seen
                ):
                    seen.add(text)
                    paths.append(text)

    # Regex fallback on raw text
    if len(paths) < max_paths:
        for m in _PATH_LIKE_RE.finditer(raw):
            p = m.group(1)
            if _is_non_canonical_runtime_path(p):
                continue
            if p not in seen:
                seen.add(p)
                paths.append(p)
            if len(paths) >= max_paths:
                break

    return paths[:max_paths]


# ---------------------------------------------------------------------------
# Pydantic models for structured LLM responses
# ---------------------------------------------------------------------------


class ToolCallRequest(BaseModel):
    """Tool call request from executor LLM."""
    name: str = Field(description="Tool name: code_executor, web_search, document_reader, etc.")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Tool parameters")


class ExecutionResponse(BaseModel):
    """Structured payload returned by the execution LLM."""

    status: str = Field(pattern="^(success|failed|skipped|needs_tool)$")
    content: str
    tool_call: Optional[ToolCallRequest] = Field(default=None, description="Tool call request when status is needs_tool")
    notes: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def model_validate_json(cls, raw: str) -> "ExecutionResponse":
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValidationError([exc], cls) from exc
        return super().model_validate(payload)


@dataclass
class ExecutionResult:
    """Execution outcome for a single task."""

    plan_id: int
    task_id: int
    status: str
    content: str
    notes: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    raw_response: Optional[str] = None
    attempts: int = 1
    duration_sec: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "task_id": self.task_id,
            "status": self.status,
            "content": self.content,
            "notes": list(self.notes),
            "metadata": dict(self.metadata),
            "raw_response": self.raw_response,
            "attempts": self.attempts,
            "duration_sec": self.duration_sec,
        }


@dataclass
class ExecutionSummary:
    """Aggregate results for execute_plan."""

    plan_id: int
    executed_task_ids: List[int] = field(default_factory=list)
    failed_task_ids: List[int] = field(default_factory=list)
    skipped_task_ids: List[int] = field(default_factory=list)
    results: List[ExecutionResult] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    @property
    def duration_sec(self) -> Optional[float]:
        if self.finished_at is None:
            return None
        return self.finished_at - self.started_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "executed_task_ids": list(self.executed_task_ids),
            "failed_task_ids": list(self.failed_task_ids),
            "skipped_task_ids": list(self.skipped_task_ids),
            "results": [result.to_dict() for result in self.results],
            "duration_sec": self.duration_sec,
        }


@dataclass
class ExecutionConfig:
    """Per-run execution options."""

    model: Optional[str] = None
    max_retries: int = 2
    timeout: Optional[float] = None
    use_context: bool = True
    include_plan_outline: bool = True
    dependency_throttle: bool = True
    max_tasks: Optional[int] = None
    session_context: Optional[Dict[str, Any]] = None
    enforce_dependencies: bool = True
    paper_mode: bool = False
    force_rerun: bool = False
    auto_recovery: bool = False
    max_recovery_attempts: int = 2
    autonomous: bool = False
    on_task_complete: Optional[Callable[["ExecutionResult", int, int], None]] = None
    enable_skills: bool = True
    skill_budget_chars: int = 6000
    skill_selection_mode: str = "hybrid"
    skill_max_per_task: int = 3
    skill_trace_enabled: bool = True
    skip_preflight: bool = False

    def __post_init__(self) -> None:
        if self.autonomous:
            self.auto_recovery = True
            self.dependency_throttle = False

    @classmethod
    def from_settings(cls, settings: ExecutorSettings) -> "ExecutionConfig":
        return cls(
            model=settings.model,
            max_retries=max(1, settings.max_retries),
            timeout=settings.timeout,
            use_context=settings.use_context,
            include_plan_outline=settings.include_plan_outline,
            dependency_throttle=settings.dependency_throttle,
            max_tasks=settings.max_tasks,
            enforce_dependencies=getattr(settings, "enforce_dependencies", True),
            paper_mode=bool(getattr(settings, "paper_mode", False)),
            force_rerun=getattr(settings, "force_rerun", False),
            auto_recovery=getattr(settings, "auto_recovery", False),
            max_recovery_attempts=max(1, getattr(settings, "max_recovery_attempts", 2)),
            autonomous=getattr(settings, "autonomous", False),
            enable_skills=settings.enable_skills,
            skill_budget_chars=settings.skill_budget_chars,
            skill_selection_mode=settings.skill_selection_mode,
            skill_max_per_task=settings.skill_max_per_task,
            skill_trace_enabled=settings.skill_trace_enabled,
        )


# ---------------------------------------------------------------------------
# Prompt builder and LLM façade
# ---------------------------------------------------------------------------


class ExecutorPromptBuilder:
    """Compose prompts for the execution LLM."""

    SYSTEM_HEADER = (
        "You are an execution agent that completes research or engineering tasks. "
        "When a task requires external tools (data retrieval, code execution, file operations), "
        "use the provided function-calling tools. "
        "When a task is text-only (design, planning, writing, analysis), respond with plain text content directly."
    )

    TOOL_HINTS = (
        "\n=== TOOL SELECTION HINTS ===\n"
        "- FASTA/accession download → sequence_fetch\n"
        "- Bioinformatics analysis (FASTA/FASTQ) → bio_tools (call help first)\n"
        "- Literature/PubMed search → literature_pipeline\n"
        "- Data analysis/visualization/code → code_executor\n"
        "- Web information lookup → web_search\n"
        "- Read documents (PDF/TXT) → document_reader\n"
        "- Read images/scanned docs → vision_reader\n"
        "- PhageScope operations → phagescope\n"
        "- Design/planning/text writing → respond directly, no tool needed"
    )

    # Rough char-to-token ratio ~3.5 for mixed EN/ZH text.
    # Cap at ~28k tokens (~100k chars) to stay within typical model context windows.
    MAX_PROMPT_CHARS = 100_000

    def _summarize_long_result(self, result: str, max_length: int = 2000) -> str:
        """Summarize long execution results to avoid prompt bloat.

        Preserves key information like:
        - Numbers, metrics, statistics
        - File paths
        - Conclusions and key findings
        - Error messages

        Args:
            result: The original result text
            max_length: Maximum length to return

        Returns:
            Summarized result if over max_length, otherwise original
        """
        if not result or len(result) <= max_length:
            return result

        # Strategy: Keep beginning and end, add truncation notice
        # Beginning often has summary/conclusion
        # End often has final results or file paths
        keep_start = int(max_length * 0.6)
        keep_end = int(max_length * 0.3)

        # Extract key patterns to preserve
        import re

        # Find file paths
        file_paths = re.findall(r'[\w/\-\.]+\.(csv|json|txt|png|jpg|pdf|fasta|fa|fq|xlsx)', result)

        # Find numbers with context (e.g., "accuracy: 0.95", "rows: 1000")
        metrics = re.findall(r'\b\w+[:\s=]+\d+\.?\d*%?\b', result)[:5]

        # Build summary
        summary_parts = [
            result[:keep_start].strip(),
            "\n... [TRUNCATED - original was {} chars] ...\n".format(len(result)),
        ]

        if file_paths:
            summary_parts.append(f"[Key files: {', '.join(set(file_paths[:5]))}]")

        if metrics:
            summary_parts.append(f"[Key metrics: {'; '.join(metrics[:5])}]")

        summary_parts.append(result[-keep_end:].strip())

        return "\n".join(summary_parts)

    def build(
        self,
        *,
        node: PlanNode,
        parent: Optional[PlanNode],
        dependencies: List[PlanNode],
        plan_outline: Optional[str],
        include_context: bool,
        session_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        lines: List[str] = [self.SYSTEM_HEADER]

        if session_context:
            user_message = session_context.get("user_message")
            if user_message:
                lines.append("\n=== USER REQUEST ===")
                lines.append(f"{user_message}")

            chat_history = session_context.get("chat_history", [])
            if chat_history:
                lines.append("\n=== RECENT CONVERSATION ===")
                recent_history = chat_history[-6:] if len(chat_history) > 6 else chat_history
                for msg in recent_history:
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    if len(content) > 500:
                        content = content[:500] + "..."
                    lines.append(f"[{role}]: {content}")

            tool_results = session_context.get("recent_tool_results", [])
            if tool_results:
                lines.append("\n=== RECENT TOOL RESULTS ===")
                for tr in tool_results[-3:]:  # 3result
                    tool_name = tr.get("tool", "unknown")
                    summary = tr.get("summary", "")
                    lines.append(f"- {tool_name}: {summary}")

        lines.append("\n=== TASK SUMMARY ===")
        lines.append(f"Task ID: {node.id}")
        lines.append(f"Task Name: {node.display_name()}")
        if node.instruction:
            lines.append(f"Instruction: {node.instruction.strip()}")
        lines.append(f"Path: {node.path}")
        node_metadata = node.metadata if isinstance(node.metadata, dict) else {}
        paper_mode = bool(
            node_metadata.get("paper_mode")
            or (session_context or {}).get("paper_mode")
        )
        paper_section = (
            str(node_metadata.get("paper_section")).strip()
            if node_metadata.get("paper_section") is not None
            else ""
        )
        paper_role = (
            str(node_metadata.get("paper_role")).strip()
            if node_metadata.get("paper_role") is not None
            else ""
        )
        raw_paper_context_paths = node_metadata.get("paper_context_paths")
        paper_context_paths = (
            [str(item).strip() for item in raw_paper_context_paths if str(item).strip()]
            if isinstance(raw_paper_context_paths, list)
            else []
        )
        artifact_contract = node_metadata.get("artifact_contract") if isinstance(node_metadata.get("artifact_contract"), dict) else {}
        resolved_input_artifacts = (
            (session_context or {}).get("resolved_input_artifacts")
            if isinstance((session_context or {}).get("resolved_input_artifacts"), dict)
            else {}
        )
        if paper_mode:
            lines.append("\n=== PAPER MODE ===")
            if paper_section:
                lines.append(f"- paper_section: {paper_section}")
            if paper_role:
                lines.append(f"- paper_role: {paper_role}")
            if isinstance(artifact_contract.get("requires"), list) and artifact_contract["requires"]:
                lines.append(f"- artifact_requires: {artifact_contract['requires']}")
            if isinstance(artifact_contract.get("publishes"), list) and artifact_contract["publishes"]:
                lines.append(f"- artifact_publishes: {artifact_contract['publishes']}")
            if resolved_input_artifacts:
                lines.append("- resolved_input_artifacts:")
                for alias, path in list(resolved_input_artifacts.items())[:10]:
                    lines.append(f"  - {alias}: {path}")
            if paper_context_paths:
                lines.append("- paper_context_paths:")
                for path in paper_context_paths[:10]:
                    lines.append(f"  - {path}")
            lines.extend(
                [
                    "- Execution template:",
                    "  1) evidence organization task -> produce artifact paths and references",
                    "  2) section tasks -> write focused section drafts",
                    "  3) assembly task -> call manuscript_writer main chain",
                    "  4) citation integrity check -> block success on citekey mismatch",
                ]
            )
        if parent:
            lines.append("\n=== PARENT TASK ===")
            lines.append(f"Parent ID: {parent.id}")
            lines.append(f"Parent Name: {parent.display_name()}")
            if parent.instruction:
                lines.append(f"Parent Instruction: {parent.instruction.strip()}")
            if parent.execution_result:
                parent_result = self._summarize_long_result(parent.execution_result, max_length=2000)
                lines.append(f"Parent Latest Result: {parent_result}")

        if dependencies:
            lines.append("\n=== DEPENDENCIES ===")
            for dep in dependencies:
                dep_status = (dep.status or "").strip().lower()
                status_indicator = (
                    "✓"
                    if dep_status in {"completed", "done"}
                    else "✗"
                    if dep_status == "failed"
                    else "○"
                )
                raw_result = dep.execution_result or "(not executed)"
                summary = self._summarize_long_result(raw_result, max_length=2000)
                lines.append(f"- [{status_indicator}] [{dep.id}] {dep.display_name()}: {summary}")

        if include_context:
            lines.append("\n=== CONTEXT ===")
            if node.context_combined:
                lines.append(f"Summary: {node.context_combined}")
            if node.context_sections:
                for section in node.context_sections:
                    title = section.get("title") or "Section"
                    content = section.get("content") or ""
                    lines.append(f"- {title}: {content}")
            if not node.context_combined and not node.context_sections:
                lines.append("(no additional context)")

        if plan_outline:
            lines.append("\n=== PLAN OUTLINE (TRUNCATED) ===")
            lines.append(plan_outline)

        lines.append(self.TOOL_HINTS)
        prompt = "\n".join(lines)
        if len(prompt) > self.MAX_PROMPT_CHARS:
            logger.warning(
                "Executor prompt too long (%d chars), truncating plan outline and dependencies.",
                len(prompt),
            )
            # Rebuild without plan outline and with shorter dependency summaries.
            # Remove the plan outline section to reclaim space.
            marker = "\n=== PLAN OUTLINE (TRUNCATED) ==="
            idx = prompt.find(marker)
            if idx != -1:
                end_idx = prompt.find("\n===", idx + len(marker))
                if end_idx != -1:
                    prompt = prompt[:idx] + "\n[Plan outline omitted due to prompt size limit]\n" + prompt[end_idx:]
            # Hard-truncate if still too long.
            if len(prompt) > self.MAX_PROMPT_CHARS:
                prompt = prompt[: self.MAX_PROMPT_CHARS] + "\n... [TRUNCATED]"
        return prompt


def _strip_code_fences(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        while lines and lines[-1].strip().startswith("```"):
            lines.pop()
        cleaned = "\n".join(lines).strip()
    return cleaned


class PlanExecutorLLMService:
    """Wrapper around LLMService dedicated to execution prompts."""

    def __init__(
        self,
        *,
        llm: Optional[LLMService] = None,
        settings: Optional[ExecutorSettings] = None,
    ) -> None:
        self._settings = settings or get_executor_settings()
        if llm is not None:
            self._llm = llm
        else:
            client: Optional[LLMClient] = None
            if any((self._settings.provider, self._settings.api_url, self._settings.api_key)):
                client = LLMClient(
                    provider=self._settings.provider,
                    api_key=self._settings.api_key,
                    url=self._settings.api_url,
                    model=self._settings.model,
                )
            self._llm = LLMService(client)
        # Store direct client reference for native tool calling
        # (LLMService.client is always set — either the passed-in client or
        # the default one created by get_default_client()).
        self._llm_client: LLMClient = self._llm.client  # type: ignore[assignment]

    def generate(
        self,
        prompt: str,
        config: ExecutionConfig,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> ExecutionResponse:
        kwargs: Dict[str, Any] = {}
        model = config.model or self._settings.model
        if model:
            kwargs["model"] = model

        if tools is not None:
            return self._generate_with_tools(prompt, tools, **kwargs)

        # Legacy fallback (no tools)
        if config.timeout is not None:
            kwargs["timeout"] = config.timeout
        response_text = self._llm.chat(prompt, **kwargs)
        cleaned = _strip_code_fences(response_text)
        try:
            return ExecutionResponse.model_validate_json(cleaned)
        except ValidationError:
            logger.error("Failed to parse execution response: %s", cleaned)
            raise

    # ------------------------------------------------------------------
    # Status inference for text-only responses in native tool-calling mode
    # ------------------------------------------------------------------

    _FAILURE_TOKENS = (
        "traceback",
        "exception",
        "failed",
        "error:",
        "unable to",
        "timed out",
        "cannot complete",
        "cannot be completed",
        "not possible",
        "无法完成",
        "执行失败",
        "出错",
        "异常",
    )

    _BLOCKED_TOKENS = (
        "blocked by dependencies",
        "dependency outputs are missing",
        "incomplete dependencies",
        "unmet dependencies",
        "prerequisite",
        "requires output from",
        "waiting for",
        "被依赖阻断",
        "依赖未完成",
        "前置任务",
    )

    _SKIPPED_TOKENS = (
        "skipped",
        "not applicable",
        "out of scope",
        "already completed",
        "已跳过",
        "不适用",
        "已完成",
    )

    @classmethod
    def _infer_text_response_status(cls, content: str) -> str:
        """Infer execution status from a text-only LLM response.

        When the native tool-calling path receives no tool_calls, the LLM
        replied with prose.  We apply heuristic detection to avoid mapping
        refusals, blockers, and failures to 'success'.
        """
        if not content or not content.strip():
            return "success"

        lowered = content.strip().lower()

        # Check blocked/dependency signals first (more specific)
        if any(token in lowered for token in cls._BLOCKED_TOKENS):
            return "skipped"

        # Check failure signals
        if any(token in lowered for token in cls._FAILURE_TOKENS):
            return "failed"

        # Check skipped signals
        if any(token in lowered for token in cls._SKIPPED_TOKENS):
            # "already completed" is ambiguous — could mean success
            if "already completed" in lowered or "已完成" in lowered:
                return "success"
            return "skipped"

        return "success"

    def _generate_with_tools(
        self,
        prompt: str,
        tools: List[Dict[str, Any]],
        **kwargs: Any,
    ) -> ExecutionResponse:
        """Call LLM with native tool calling, synchronously."""
        # Split prompt into system + user messages.
        # The first line is the SYSTEM_HEADER from ExecutorPromptBuilder.
        system_msg = prompt.split("\n", 1)[0]
        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": prompt},
        ]

        async def _call() -> NativeStreamResult:
            return await self._llm_client.stream_chat_with_tools_async(
                messages=messages,
                tools=tools,
                tool_choice="auto",
                **kwargs,
            )

        # Bridge async → sync using the module-level helper.
        result: NativeStreamResult = _run_coroutine_sync(_call())

        # Convert NativeStreamResult → ExecutionResponse
        if result.tool_calls:
            first_tc = result.tool_calls[0]
            return ExecutionResponse(
                status="needs_tool",
                content=result.content or "",
                tool_call=ToolCallRequest(
                    name=first_tc.name,
                    parameters=first_tc.arguments,
                ),
            )
        else:
            content = result.content or ""
            status = self._infer_text_response_status(content)
            return ExecutionResponse(
                status=status,
                content=content,
            )


# ---------------------------------------------------------------------------
# Main executor façade
# ---------------------------------------------------------------------------


class PlanExecutor:
    """Execute plan tasks using a dedicated LLM."""

    def __init__(
        self,
        *,
        repo: Optional["PlanRepository"] = None,
        llm_service: Optional[PlanExecutorLLMService] = None,
        settings: Optional[ExecutorSettings] = None,
        prompt_builder: Optional[ExecutorPromptBuilder] = None,
    ) -> None:
        if repo is None:
            from ...repository.plan_repository import PlanRepository

            repo = PlanRepository()
        self._repo = repo
        self._settings = settings or get_executor_settings()
        self._llm = llm_service or PlanExecutorLLMService(settings=self._settings)
        self._prompt_builder = prompt_builder or ExecutorPromptBuilder()
        self._deliverable_publisher = get_deliverable_publisher()
        self._tool_executor = UnifiedToolExecutor()
        self._task_verifier = TaskVerificationService()
        self._artifact_preflight = ArtifactPreflightService()
        self._status_resolver = PlanStatusResolver()

    def _ensure_runtime_helpers(self) -> None:
        if not hasattr(self, "_settings"):
            self._settings = get_executor_settings()
        if not hasattr(self, "_artifact_preflight"):
            self._artifact_preflight = ArtifactPreflightService()
        if not hasattr(self, "_status_resolver"):
            self._status_resolver = PlanStatusResolver()
        if not hasattr(self, "_task_verifier"):
            self._task_verifier = TaskVerificationService()

    def _resolve_task_tool_workspace(
        self,
        node: PlanNode,
        *,
        session_id: Optional[str],
        tree: Optional[PlanTree] = None,
    ) -> Tuple[Optional[List[int]], str]:
        """Return the canonical task-scoped workspace for tool execution."""

        from app.services.path_router import PathRouter, get_path_router

        resolved_tree = tree
        if resolved_tree is None:
            try:
                resolved_tree = self._repo.get_plan_tree(node.plan_id)
            except Exception:
                resolved_tree = None

        ancestor_chain: Optional[List[int]] = None
        if resolved_tree is not None:
            try:
                ancestor_chain = PathRouter.build_ancestor_chain(node.id, resolved_tree)
            except Exception:
                ancestor_chain = None

        effective_session_id = session_id or "adhoc"
        try:
            work_dir = str(
                get_path_router().get_task_output_dir(
                    effective_session_id,
                    node.id,
                    ancestor_chain,
                    create=True,
                )
            )
        except Exception:
            work_dir = os.getcwd()

        return ancestor_chain, work_dir

    def execute_plan(
        self,
        plan_id: int,
        *,
        config: Optional[ExecutionConfig] = None,
    ) -> ExecutionSummary:
        self._ensure_runtime_helpers()
        cfg = config or ExecutionConfig.from_settings(self._settings)
        summary = ExecutionSummary(plan_id=plan_id)
        tree = self._repo.get_plan_tree(plan_id)
        preflight = self._artifact_preflight.validate_plan(plan_id, tree)
        if preflight.has_errors() and not cfg.skip_preflight:
            summary.finished_at = time.time()
            failed_task_ids = preflight.affected_task_ids() or [0]
            summary.failed_task_ids.extend(failed_task_ids)
            summary.results.append(
                ExecutionResult(
                    plan_id=plan_id,
                    task_id=failed_task_ids[0],
                    status="failed",
                    content=preflight.summary(),
                    metadata={"preflight": preflight.model_dump()},
                )
            )
            _log_job(
                "error",
                "Plan execution blocked by artifact preflight.",
                {
                    "plan_id": plan_id,
                    "issues": [issue.model_dump() for issue in preflight.errors],
                },
            )
            return summary
        if preflight.has_errors() and cfg.skip_preflight:
            _log_job(
                "warning",
                "Artifact preflight has errors but skip_preflight=True; continuing execution.",
                {
                    "plan_id": plan_id,
                    "issue_count": len(preflight.errors),
                },
            )
        tree = self._infer_missing_dependencies(tree)
        order = list(self._execution_order(tree))

        if cfg.max_tasks is not None:
            order = order[: cfg.max_tasks]

        _log_job(
            "info",
            "Plan execution started.",
            {"plan_id": plan_id, "task_count": len(order)},
        )

        if not order:
            summary.finished_at = time.time()
            _log_job(
                "warning",
                "The plan has no executable tasks.",
                {"plan_id": plan_id},
            )
            return summary

        # --- Plan-level skill pre-selection ---
        if cfg.enable_skills:
            self._preselect_skills_for_plan(tree, cfg)

        # Layer 2: Artifact registry — accumulates output paths across tasks
        # so that downstream tasks can reference upstream outputs directly.
        artifact_registry: Dict[int, List[str]] = {}
        if cfg.session_context is None:
            cfg.session_context = {}

        # Track failed/skipped task ids for transitive dependency skip
        _failed_or_skipped: set = set()
        cfg.session_context["_artifact_registry"] = artifact_registry
        cfg.session_context["_artifact_manifest"] = self._get_artifact_manifest(plan_id, cfg.session_context)

        # Layer 3: Recovery attempt tracker per task
        recovery_attempts: Dict[int, int] = {}

        def _remove_task_from_status_buckets(task_id: int) -> None:
            for bucket in (
                summary.executed_task_ids,
                summary.failed_task_ids,
                summary.skipped_task_ids,
            ):
                try:
                    bucket.remove(task_id)
                except ValueError:
                    pass

        def _collect_artifacts(task_id: int, exec_result: ExecutionResult) -> None:
            try:
                raw_payload = exec_result.raw_response or exec_result.content or ""
                paths = _extract_paths_from_execution_result(raw_payload)
                if paths:
                    artifact_registry[task_id] = paths
            except Exception:
                pass

        def _record_final_result(exec_result: ExecutionResult, *, replace_existing: bool = False) -> None:
            if replace_existing:
                summary.results = [
                    existing
                    for existing in summary.results
                    if existing.task_id != exec_result.task_id
                ]
            summary.results.append(exec_result)
            _remove_task_from_status_buckets(exec_result.task_id)
            if exec_result.status == "completed":
                summary.executed_task_ids.append(exec_result.task_id)
                _collect_artifacts(exec_result.task_id, exec_result)
            elif exec_result.status == "skipped":
                summary.skipped_task_ids.append(exec_result.task_id)
            else:
                summary.failed_task_ids.append(exec_result.task_id)

        total_tasks = len(order)
        for idx, node in enumerate(order):
            # --- Layer 1: skip already-completed tasks (resume support) ---
            plan_state_by_task = self._status_resolver.resolve_plan_states(
                plan_id,
                tree,
                manifest=self._get_artifact_manifest(plan_id, cfg.session_context),
            )
            node_effective_status = str(
                (plan_state_by_task.get(node.id) or {}).get("effective_status") or ""
            ).strip().lower()
            if node_effective_status == "completed" and not cfg.force_rerun:
                summary.executed_task_ids.append(node.id)
                _log_job("info", "Skipping already-completed task.", {
                    "plan_id": plan_id, "task_id": node.id,
                    "task_name": node.display_name(),
                })
                continue

            # --- Transitive dependency skip (autonomous mode) ---
            node_deps = set(getattr(node, "dependencies", None) or [])
            blocked_by = node_deps & _failed_or_skipped
            if blocked_by:
                _failed_or_skipped.add(node.id)
                skip_result = ExecutionResult(
                    plan_id=plan_id,
                    task_id=node.id,
                    status="skipped",
                    content=f"Skipped: upstream task(s) {sorted(blocked_by)} failed or were skipped",
                    metadata={"skipped_reason": "upstream_failed", "blocked_by": sorted(blocked_by)},
                )
                _record_final_result(skip_result)
                _log_job("warning", "Skipping task due to upstream failure.", {
                    "plan_id": plan_id, "task_id": node.id,
                    "blocked_by": sorted(blocked_by),
                })
                continue

            # --- Layer 1: progress event ---
            completed_count = len(summary.executed_task_ids)
            _log_job("info", "Plan execution progress.", {
                "plan_id": plan_id,
                "current_task": node.id,
                "task_name": node.display_name(),
                "completed": completed_count,
                "total": total_tasks,
                "progress_pct": round(completed_count / total_tasks * 100) if total_tasks else 0,
            })

            start = time.time()
            _log_job(
                "info",
                "Starting plan task execution.",
                {
                    "plan_id": plan_id,
                    "task_id": node.id,
                    "task_name": node.display_name(),
                },
            )
            try:
                result = self._run_task(plan_id, node, tree, cfg)
            except Exception as exc:
                logger.exception(
                    "Execution failed for plan %s task %s: %s",
                    plan_id,
                    node.id,
                    exc,
                )
                result = ExecutionResult(
                    plan_id=plan_id,
                    task_id=node.id,
                    status="failed",
                    content=str(exc),
                    notes=[f"Exception: {exc}"],
                )

            result.duration_sec = (time.time() - start) if result.duration_sec is None else result.duration_sec

            # --- Layer 3: automatic recovery for failed AND skipped-by-dependency ---
            # We attempt recovery BEFORE appending to summary so that the
            # final summary.results reflects the true outcome.
            needs_recovery = False
            if result.status in ("failed", "skipped"):
                needs_recovery = (
                    cfg.auto_recovery
                    and recovery_attempts.get(node.id, 0) < cfg.max_recovery_attempts
                )

            recovered = False
            if needs_recovery:
                try:
                    from app.services.plans.failure_recovery import (
                        FailureAnalyzer, RECOVERABLE, FailureCategory,
                    )
                    category = FailureAnalyzer().classify(
                        result.content or "",
                        {},
                        result_status=result.status,
                        result_metadata=result.metadata,
                    )
                    if category in RECOVERABLE:
                        recovery_attempts[node.id] = recovery_attempts.get(node.id, 0) + 1
                        _log_job("warning", f"Task {result.status} ({category.value}), attempting recovery.", {
                            "task_id": node.id,
                            "attempt": recovery_attempts[node.id],
                            "max_attempts": cfg.max_recovery_attempts,
                        })
                        if category == FailureCategory.UPSTREAM_INCOMPLETE:
                            # Re-run upstream dependencies first. If the task was
                            # skipped by enforce_dependencies, only incomplete
                            # deps need a rerun. If the task failed with a blocked
                            # dependency despite completed upstream status, we may
                            # need to regenerate completed dependency outputs too.
                            incomplete_dep_ids = result.metadata.get("incomplete_dependencies") or []
                            dep_ids_to_rerun = incomplete_dep_ids if incomplete_dep_ids else (node.dependencies or [])
                            force_rerun_completed = not bool(
                                result.metadata.get("blocked_by_dependencies")
                            )
                            dependency_recovery_failed_result: Optional[ExecutionResult] = None
                            for dep_id in dep_ids_to_rerun:
                                if not tree.has_node(dep_id):
                                    continue
                                dep = tree.get_node(dep_id)
                                dep_st = (dep.status or "").strip().lower()
                                if dep_st in ("completed", "done") and not force_rerun_completed:
                                    continue
                                dep.status = "pending"
                                dep_result = self._run_task(plan_id, dep, tree, cfg)
                                tree.nodes[dep_id] = dep
                                _record_final_result(dep_result, replace_existing=True)
                                if dep_result.status != "completed":
                                    dependency_recovery_failed_result = dep_result
                                    break
                            if dependency_recovery_failed_result is not None:
                                failed_dep_id = dependency_recovery_failed_result.task_id
                                dep_status = dependency_recovery_failed_result.status
                                dep_reason = (
                                    dependency_recovery_failed_result.content
                                    or "upstream dependency recovery failed"
                                )
                                result = ExecutionResult(
                                    plan_id=plan_id,
                                    task_id=node.id,
                                    status="skipped",
                                    content=(
                                        f"Blocked by dependencies after recovery attempt: "
                                        f"task #{failed_dep_id} ended with status={dep_status}. "
                                        f"Latest upstream detail: {dep_reason}"
                                    ),
                                    notes=[
                                        "Automatic recovery stopped because an upstream dependency rerun did not complete successfully."
                                    ],
                                    metadata={
                                        "blocked_by_dependencies": True,
                                        "incomplete_dependencies": [failed_dep_id],
                                        "dependency_recovery_failed": True,
                                        "failed_dependency_status": dep_status,
                                        "failed_dependency_task_id": failed_dep_id,
                                    },
                                    attempts=result.attempts,
                                )
                                result.duration_sec = (time.time() - start)
                            else:
                                # Retry the current task only when all selected
                                # dependency reruns finished successfully.
                                node.status = "pending"
                                retry_result = self._run_task(plan_id, node, tree, cfg)
                                result = retry_result
                                result.duration_sec = (time.time() - start)
                                if retry_result.status == "completed":
                                    recovered = True
                        else:
                            node.status = "pending"
                            retry_result = self._run_task(plan_id, node, tree, cfg)
                            result = retry_result
                            result.duration_sec = (time.time() - start)
                            if retry_result.status == "completed":
                                recovered = True
                except ImportError:
                    logger.debug("failure_recovery module not available, skipping auto-recovery")
                except Exception as rec_err:
                    logger.warning("Auto-recovery failed for task %s: %s", node.id, rec_err)

            _record_final_result(result, replace_existing=True)

            if result.status == "skipped":
                if not recovered:
                    _failed_or_skipped.add(node.id)
                    if cfg.dependency_throttle:
                        logger.warning(
                            "Stopping execution for plan %s: task %s blocked by unresolved dependencies",
                            plan_id,
                            node.id,
                        )
                        _log_job(
                            "warning",
                            "Plan execution stopped: dependency blockage unresolvable.",
                            {"plan_id": plan_id, "blocked_task_id": node.id},
                        )
                        break
            elif result.status != "completed":
                if not recovered:
                    _failed_or_skipped.add(node.id)
                    if cfg.dependency_throttle:
                        logger.warning(
                            "Stopping execution for plan %s due to failure on task %s",
                            plan_id,
                            node.id,
                        )
                        _log_job(
                            "warning",
                            "Plan execution stopped: unrecoverable failure.",
                            {"plan_id": plan_id, "failed_task_id": node.id},
                        )
                        break

            # Log the FINAL status (after potential recovery)
            level = (
                "success"
                if result.status == "completed"
                else "warning"
                if result.status == "skipped"
                else "error"
            )
            _log_job(
                level,
                "Plan task execution completed.",
                {
                    "plan_id": plan_id,
                    "task_id": node.id,
                    "status": result.status,
                    "recovered": recovered,
                    "duration_sec": result.duration_sec,
                },
            )

            # Fire on_task_complete callback
            if cfg.on_task_complete is not None:
                try:
                    cfg.on_task_complete(result, idx + 1, total_tasks)
                except Exception as cb_err:
                    logger.warning("on_task_complete callback error: %s", cb_err)

        summary.finished_at = time.time()

        if summary.executed_task_ids:
            try:
                plan_summary = self._generate_plan_summary(plan_id, tree, summary, cfg)
                if plan_summary:
                    current_metadata = tree.metadata or {}
                    current_metadata["execution_summary"] = plan_summary
                    current_metadata["execution_summary_at"] = summary.finished_at
                    self._repo.update_plan_metadata(plan_id, current_metadata)
                    _log_job(
                        "info",
                        "Plan execution summary generated.",
                        {"plan_id": plan_id, "summary_length": len(plan_summary)},
                    )
            except Exception as exc:
                logger.warning("Failed to generate plan summary: %s", exc)

        _log_job(
            "info",
            "Plan execution finished.",
            {
                "plan_id": plan_id,
                "completed": len(summary.executed_task_ids),
                "failed": len(summary.failed_task_ids),
                "skipped": len(summary.skipped_task_ids),
            },
        )
        return summary

    def execute_task(
        self,
        plan_id: int,
        task_id: int,
        *,
        config: Optional[ExecutionConfig] = None,
    ) -> ExecutionResult:
        self._ensure_runtime_helpers()
        cfg = config or ExecutionConfig.from_settings(self._settings)
        tree = self._repo.get_plan_tree(plan_id)
        if task_id not in tree.nodes:
            raise ValueError(f"Task {task_id} not found in plan {plan_id}")
        node = tree.get_node(task_id)
        return self._run_task(plan_id, node, tree, cfg)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_task(
        self,
        plan_id: int,
        node: PlanNode,
        tree: PlanTree,
        config: ExecutionConfig,
    ) -> ExecutionResult:
        parent = tree.nodes.get(node.parent_id) if node.parent_id else None
        dependencies = self._resolve_dependencies(tree, node)

        incomplete_deps = []
        for dep in dependencies:
            dep_status = (dep.status or "pending").strip().lower()
            if dep_status not in ("completed", "done"):
                incomplete_deps.append(dep)
                logger.warning(
                    "Dependency %s (status=%s) not completed before executing task %s",
                    dep.id, dep.status, node.id
                )

        if incomplete_deps and config.enforce_dependencies:
            incomplete_ids = [d.id for d in incomplete_deps]
            incomplete_display = ", ".join(
                f"#{d.id}({(d.status or 'pending').strip()})" for d in incomplete_deps
            )
            skip_reason = (
                f"Blocked by dependencies: task #{node.id} requires completed outputs from "
                f"{len(incomplete_deps)} dependency task(s): {incomplete_display}."
            )
            _log_job(
                "warning",
                f"Task {node.id} skipped: dependencies not satisfied",
                {
                    "task_id": node.id,
                    "incomplete_deps": incomplete_ids,
                    "enforce_dependencies": True,
                },
            )
            notes = [
                "This task was not executed because dependency outputs are missing.",
                f"Unmet dependencies: {incomplete_display}",
            ]
            metadata = {
                "blocked_by_dependencies": True,
                "incomplete_dependencies": incomplete_ids,
                "incomplete_dependency_info": [
                    {"id": d.id, "name": d.display_name(), "status": d.status}
                    for d in incomplete_deps
                ],
                "enforce_dependencies": True,
            }
            payload = {
                "status": "skipped",
                "content": skip_reason,
                "notes": notes,
                "metadata": metadata,
            }
            finalization = self._task_verifier.finalize_payload(
                node,
                payload,
                execution_status="skipped",
            )
            raw_response = json.dumps(finalization.payload, ensure_ascii=False)

            # Persist skip reason so the UI can render why it was skipped.
            try:
                self._persist_execution(
                    plan_id,
                    node.id,
                    finalization.payload,
                    status=finalization.final_status,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to persist skipped execution result for task %s: %s",
                    node.id,
                    exc,
                )
                try:
                    self._repo.update_task(plan_id, node.id, status="skipped")
                except Exception as inner_exc:  # pragma: no cover - defensive
                    logger.warning(
                        "Failed to update task %s status to skipped: %s",
                        node.id,
                        inner_exc,
                    )

            # Update in-memory tree so subsequent tasks see latest status/result.
            node.status = finalization.final_status
            node.execution_result = raw_response
            tree.nodes[node.id] = node

            return ExecutionResult(
                plan_id=plan_id,
                task_id=node.id,
                status=finalization.final_status,
                content=skip_reason,
                notes=notes,
                metadata=finalization.payload.get("metadata") or {},
                raw_response=raw_response,
            )
        elif incomplete_deps:
            _log_job(
                "warning",
                f"Task {node.id} has {len(incomplete_deps)} incomplete dependencies (continuing anyway)",
                {
                    "task_id": node.id,
                    "incomplete_deps": [d.id for d in incomplete_deps],
                    "enforce_dependencies": False,
                },
            )

        if config.session_context is None:
            config.session_context = {}
        session_context = config.session_context if isinstance(config.session_context, dict) else {}
        artifact_contract, resolved_input_artifacts, missing_aliases, producer_map = self._resolve_required_artifacts(
            plan_id,
            node,
            dependencies=dependencies,
            tree=tree,
            session_context=session_context,
        )
        if isinstance(node.metadata, dict):
            node.metadata.setdefault("artifact_contract", artifact_contract)
            raw_paths = node.metadata.get("paper_context_paths")
            merged_paths = [
                str(item).strip()
                for item in raw_paths
                if isinstance(item, str) and str(item).strip()
            ] if isinstance(raw_paths, list) else []
            for path in resolved_input_artifacts.values():
                if path not in merged_paths:
                    merged_paths.append(path)
            if merged_paths:
                node.metadata["paper_context_paths"] = merged_paths[:40]
        if session_context is not None:
            session_context["resolved_input_artifacts"] = dict(resolved_input_artifacts)
        if missing_aliases:
            _log_job(
                "warning",
                f"Task {node.id} blocked: required artifacts not published",
                {
                    "task_id": node.id,
                    "missing_artifact_aliases": missing_aliases,
                    "producer_task_candidates": producer_map,
                },
            )
            return self._block_for_missing_artifacts(
                plan_id=plan_id,
                node=node,
                tree=tree,
                missing_aliases=missing_aliases,
                producer_candidates=producer_map,
                resolved_input_artifacts=resolved_input_artifacts,
            )

        outline = tree.to_outline(max_depth=4, max_nodes=80) if config.include_plan_outline else None

        if self._should_use_deep_think(config):
            return self._run_task_with_deep_think(
                plan_id=plan_id,
                node=node,
                parent=parent,
                dependencies=dependencies,
                plan_outline=outline,
                tree=tree,
                config=config,
            )

        prompt = self._prompt_builder.build(
            node=node,
            parent=parent,
            dependencies=dependencies,
            plan_outline=outline,
            include_context=config.use_context,
            session_context=config.session_context,
        )

        attempts = max(1, config.max_retries)
        last_error: Optional[Exception] = None
        raw_response: Optional[str] = None
        try:
            self._repo.update_task(plan_id, node.id, status="running", execution_result="")
            node.status = "running"
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Failed to mark task %s as running for plan %s: %s",
                node.id,
                plan_id,
                exc,
            )
        for attempt in range(1, attempts + 1):
            try:
                _log_job(
                    "info",
                    "Starting task execution attempt.",
                    {
                        "plan_id": plan_id,
                        "task_id": node.id,
                        "attempt": attempt,
                    },
                )
                _log_job("info", "LLM call started.", {
                    "sub_type": "llm_call_start",
                    "task_id": node.id,
                    "attempt": attempt,
                })
                response = self._llm.generate(prompt, config, tools=build_executor_tool_schemas())
                _log_job("info", "LLM call completed.", {
                    "sub_type": "llm_call_end",
                    "task_id": node.id,
                    "attempt": attempt,
                    "parsed_status": response.status,
                    "tool_call_requested": response.tool_call is not None,
                })

                if response.status == "needs_tool" and response.tool_call:
                    _log_job("info", f"Tool dispatch: {response.tool_call.name}", {
                        "sub_type": "tool_dispatch",
                        "task_id": node.id,
                        "tool_name": response.tool_call.name,
                        "parameters_summary": _summarize_tool_params(response.tool_call.parameters),
                    })
                    tool_start = time.time()
                    tool_result = self._execute_tool_call(
                        response.tool_call,
                        node=node,
                        config=config,
                    )
                    tool_duration = round(time.time() - tool_start, 3)
                    _log_job(
                        "info" if tool_result.get("success") else "error",
                        f"Tool completed: {response.tool_call.name}",
                        {
                            "sub_type": "tool_complete",
                            "task_id": node.id,
                            "tool_name": response.tool_call.name,
                            "success": tool_result.get("success"),
                            "duration_sec": tool_duration,
                        },
                    )
                    # Persist action log entry for tool execution
                    try:
                        from .decomposition_jobs import get_current_job
                        from ...repository.plan_storage import append_action_log_entry

                        current_job_id = get_current_job()
                        if current_job_id:
                            append_action_log_entry(
                                plan_id=plan_id,
                                job_id=current_job_id,
                                job_type="plan_execute",
                                session_id=(config.session_context or {}).get("session_id") if config.session_context else None,
                                user_message=None,
                                action_kind="tool_call",
                                action_name=response.tool_call.name,
                                status="succeeded" if tool_result.get("success") else "failed",
                                success=tool_result.get("success"),
                                message=_summarize_tool_params(response.tool_call.parameters),
                                details={"result_summary": str(tool_result.get("summary", ""))[:500]},
                            )
                    except Exception as action_log_exc:
                        logger.warning("Failed to persist action log for tool %s: %s", response.tool_call.name, action_log_exc)
                    if tool_result.get("success"):
                        final_content = f"{response.content}\n\n=== Tool Execution Result ===\n{tool_result.get('summary', str(tool_result.get('result', '')))}"
                        response.status = "success"
                    else:
                        tool_error = tool_result.get("error")
                        if (
                            not tool_error
                            and isinstance(tool_result.get("result"), dict)
                        ):
                            tool_error = tool_result["result"].get("error")
                        final_content = f"{response.content}\n\n=== Tool Execution Failed ===\n{tool_error or 'Unknown error'}"
                        response.status = "failed"
                    deliverable_payload = tool_result.get("deliverables")
                    if isinstance(deliverable_payload, dict):
                        metadata = dict(response.metadata or {})
                        metadata["deliverables"] = deliverable_payload
                        response.metadata = metadata
                    response.content = final_content

                result_payload = response.model_dump()
                response_paths = self._extract_path_like_values(response.content)
                if response_paths:
                    existing_paths = [
                        str(item).strip()
                        for item in list(result_payload.get("artifact_paths") or [])
                        if str(item).strip()
                    ]
                    result_payload["artifact_paths"] = list(
                        dict.fromkeys([*existing_paths, *response_paths])
                    )[:40]
                    metadata = result_payload.get("metadata")
                    if not isinstance(metadata, dict):
                        metadata = {}
                        result_payload["metadata"] = metadata
                    metadata["artifact_paths"] = list(result_payload["artifact_paths"])
                raw_response = json.dumps(result_payload, ensure_ascii=False)
                task_status = self._normalize_status(response.status)
                finalization, _ = self._finalize_task_execution(
                    plan_id,
                    node,
                    result_payload,
                    execution_status=task_status,
                )
                finalization, raw_response = self._materialize_finalization(
                    plan_id,
                    node,
                    finalization,
                    session_context=config.session_context,
                )
                # Update in-memory tree so subsequent tasks see latest outputs.
                node.execution_result = raw_response
                node.status = finalization.final_status
                tree.nodes[node.id] = node
                if parent:
                    tree.nodes[parent.id] = parent
                return ExecutionResult(
                    plan_id=plan_id,
                    task_id=node.id,
                    status=finalization.final_status,
                    content=str(finalization.payload.get("content") or response.content),
                    notes=response.notes,
                    metadata=finalization.payload.get("metadata") or {},
                    raw_response=raw_response,
                    attempts=attempt,
                )
            except Exception as exc:  # pragma: no cover - retry path
                last_error = exc
                logger.warning(
                    "Attempt %s failed for plan %s task %s: %s",
                    attempt,
                    plan_id,
                    node.id,
                    exc,
                )
                _log_job(
                    "warning",
                    "Task execution attempt failed; retrying.",
                    {
                        "plan_id": plan_id,
                        "task_id": node.id,
                        "attempt": attempt,
                        "error": str(exc),
                    },
                )
                # Distinguish JSON parse errors from other failures
                if isinstance(exc, (ValidationError, json.JSONDecodeError)):
                    _log_job("warning", "LLM response JSON parse failed.", {
                        "sub_type": "json_parse_error",
                        "task_id": node.id,
                        "attempt": attempt,
                        "error": str(exc),
                    })
                else:
                    _log_job("warning", "Task execution retry triggered.", {
                        "sub_type": "retry",
                        "task_id": node.id,
                        "attempt": attempt,
                        "reason": str(exc),
                    })
                continue

        error_message = str(last_error) if last_error else "Unknown execution failure"
        finalization, _ = self._finalize_task_execution(
            plan_id,
            node,
            {
                "status": "failed",
                "content": error_message,
                "notes": ["all attempts failed"],
                "metadata": {},
            },
            execution_status="failed",
        )
        finalization, raw_response = self._materialize_finalization(
            plan_id,
            node,
            finalization,
            session_context=config.session_context,
        )
        node.execution_result = raw_response
        node.status = finalization.final_status
        tree.nodes[node.id] = node
        result = ExecutionResult(
            plan_id=plan_id,
            task_id=node.id,
            status=finalization.final_status,
            content=error_message,
            notes=["all attempts failed"],
            metadata=finalization.payload.get("metadata") or {},
            raw_response=raw_response,
            attempts=attempts,
        )
        _log_job(
            "error",
            "All task execution attempts failed.",
            {
                "plan_id": plan_id,
                "task_id": node.id,
                "attempts": attempts,
                "error": error_message,
            },
        )
        return result

    def _infer_missing_dependencies(self, tree: PlanTree) -> PlanTree:
        """Infer and persist missing dependency edges based on producer-consumer matching.

        For each task that declares ``paper_context_paths``, check whether the
        referenced file basename has a known producer (a task whose
        ``acceptance_criteria`` declares that file via ``file_exists`` or
        ``file_nonempty``).  If the producer is not already in the consumer's
        ``dependencies``, add it via ``update_task``.

        Returns the (possibly reloaded) tree.  On any error, returns the
        original tree unchanged.
        """
        try:
            # Step 1: Build producer_map {basename|artifact_alias → task_id}
            producer_map: Dict[str, int] = {}
            producer_all: Dict[str, List[int]] = {}  # for warning on conflicts
            for node in tree.nodes.values():
                metadata = node.metadata if isinstance(node.metadata, dict) else {}
                contract = self._resolve_task_artifact_contract(node)
                for alias in contract.get("publishes", []):
                    producer_all.setdefault(alias, []).append(node.id)
                    if alias not in producer_map or node.id > producer_map[alias]:
                        producer_map[alias] = node.id
                criteria = metadata.get("acceptance_criteria")
                if not isinstance(criteria, dict):
                    continue
                checks = criteria.get("checks")
                if not isinstance(checks, list):
                    continue
                for check in checks:
                    if not isinstance(check, dict):
                        continue
                    check_type = str(check.get("type") or "").strip()
                    if check_type not in ("file_exists", "file_nonempty"):
                        continue
                    raw_path = check.get("path")
                    if not isinstance(raw_path, str) or not raw_path.strip():
                        continue
                    basename = os.path.basename(raw_path.strip())
                    if not basename:
                        continue
                    producer_all.setdefault(basename, []).append(node.id)
                    # Take task_id with the largest value (latest created)
                    if basename not in producer_map or node.id > producer_map[basename]:
                        producer_map[basename] = node.id

            # Log warnings for multi-producer basenames / aliases
            for basename, ids in producer_all.items():
                if len(ids) > 1:
                    chosen = producer_map[basename]
                    logger.warning(
                        "Multiple producers for '%s': tasks %s, using task %d",
                        basename,
                        sorted(ids),
                        chosen,
                    )

            # Step 2: Find missing dependencies
            pending_additions: Dict[int, List[int]] = {}  # consumer_id → [producer_ids]
            for node in tree.nodes.values():
                metadata = node.metadata if isinstance(node.metadata, dict) else {}
                contract = self._resolve_task_artifact_contract(node)
                for alias in contract.get("requires", []):
                    producer_id = producer_map.get(alias)
                    if producer_id is None or producer_id == node.id or producer_id in node.dependencies:
                        continue
                    pending_additions.setdefault(node.id, []).append(producer_id)
                raw_paths = metadata.get("paper_context_paths")
                if not isinstance(raw_paths, list):
                    continue
                for raw_path in raw_paths:
                    if not isinstance(raw_path, str) or not raw_path.strip():
                        continue
                    basename = os.path.basename(raw_path.strip())
                    producer_id = producer_map.get(basename)
                    if producer_id is None:
                        continue
                    if producer_id == node.id:
                        continue
                    if producer_id in node.dependencies:
                        continue
                    pending_additions.setdefault(node.id, []).append(producer_id)

            if not pending_additions:
                return tree

            # Step 3: Persist new dependencies
            expected_edges: List[tuple] = []  # (consumer_id, producer_id, basename)
            for consumer_id, additions in pending_additions.items():
                node = tree.nodes[consumer_id]
                new_deps = list(node.dependencies)
                for dep_id in additions:
                    if dep_id not in new_deps:
                        new_deps.append(dep_id)
                self._repo.update_task(tree.id, consumer_id, dependencies=new_deps)
                for producer_id in additions:
                    # Find the basename that triggered this edge
                    metadata = node.metadata if isinstance(node.metadata, dict) else {}
                    raw_paths = metadata.get("paper_context_paths") or []
                    matched_basename = ""
                    for rp in raw_paths:
                        bn = os.path.basename(str(rp).strip())
                        if producer_map.get(bn) == producer_id:
                            matched_basename = bn
                            break
                    expected_edges.append((consumer_id, producer_id, matched_basename))

            # Step 4: Verify persistence and log results
            updated_tree = self._repo.get_plan_tree(tree.id)
            any_accepted = False
            for consumer_id, producer_id, basename in expected_edges:
                updated_node = updated_tree.nodes.get(consumer_id)
                if updated_node and producer_id in updated_node.dependencies:
                    logger.info(
                        "Inferred dependency: task %d -> task %d (via %s)",
                        consumer_id,
                        producer_id,
                        basename,
                    )
                    any_accepted = True
                else:
                    logger.warning(
                        "Dependency task %d -> task %d rejected (likely cycle), skipping",
                        consumer_id,
                        producer_id,
                    )

            return updated_tree if any_accepted else tree

        except Exception as exc:
            logger.warning("Failed to infer missing dependencies: %s", exc)
            return tree

    def _preselect_skills_for_plan(
        self,
        tree: PlanTree,
        config: ExecutionConfig,
    ) -> None:
        """Select plan-scope skill candidates and cache them in session_context."""
        if config.session_context is None:
            config.session_context = {}

        if "plan_skill_candidates" in config.session_context:
            return

        try:
            loader = get_skills_loader(auto_sync=True)
            available = loader.list_skills()
            if not available:
                logger.info("No skills available; skipping plan-level skill selection")
                config.session_context["plan_skill_candidates"] = []
                return

            root = None
            root_ids = tree.root_node_ids()
            if root_ids:
                root = tree.nodes.get(root_ids[0])

            plan_title = root.display_name() if root else f"Plan {tree.id}"
            plan_description = (root.instruction or "") if root else ""
            if not plan_description:
                child_names = [
                    n.display_name() for n in tree.nodes.values()
                    if n.parent_id == (root.id if root else None)
                ][:8]
                plan_description = "Sub-tasks: " + ", ".join(child_names)

            selection = self._run_coroutine_sync(
                loader.select_plan_skill_candidates(
                    plan_title=plan_title,
                    plan_description=plan_description,
                    llm_service=self._llm._llm,
                    max_skills=max(5, config.skill_max_per_task),
                    selection_mode=config.skill_selection_mode,
                )
            )
            config.session_context["plan_skill_candidates"] = (
                selection.selected_skill_ids
            )
            _log_job(
                "info",
                "Plan-level skill candidate selection completed",
                {
                    "plan_skill_candidates": selection.selected_skill_ids,
                    "selection_source": selection.selection_source,
                    "selection_latency_ms": selection.selection_latency_ms,
                },
            )
        except Exception as exc:
            logger.warning("Plan-level skill pre-selection failed (non-blocking): %s", exc)
            config.session_context["plan_skill_candidates"] = []

    def _collect_dependency_paths(
        self,
        dependencies: List[PlanNode],
        paper_context_paths: Sequence[str],
        artifact_registry: Optional[Dict[int, List[str]]] = None,
        artifact_manifest: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        seen: set[str] = set()
        ordered: List[str] = []
        for path in paper_context_paths:
            text = str(path).strip()
            if text and text not in seen:
                seen.add(text)
                ordered.append(text)
        for dep in dependencies:
            artifact_context = self._dependency_artifact_context(
                dep,
                artifact_registry,
                artifact_manifest=artifact_manifest,
            )
            artifact_paths = artifact_context.get("artifact_paths") or []
            if not isinstance(artifact_paths, list):
                continue
            for artifact_path in artifact_paths:
                text = str(artifact_path).strip()
                if text and text not in seen:
                    seen.add(text)
                    ordered.append(text)
        return ordered

    @staticmethod
    def _resolve_context_paths_from_deps(
        context_paths: List[str],
        dep_artifacts: List[Tuple[int, List[str]]],
    ) -> List[str]:
        """Resolve relative filenames in *context_paths* against dependency artifacts.

        For each relative path, search *dep_artifacts* for a basename match.

        Conflict resolution:
          - **Cross-dependency**: if multiple deps have the same basename, pick
            the artifact from the dep with the largest ``dep_id``.
          - **Same-dependency**: if one dep exposes multiple artifacts with the
            same basename, pick by path priority: paths containing
            ``deliverable/`` win over ``results/``, which win over anything
            else (fallback: last match in the list).

        Absolute paths are kept as-is.  Unmatched relative paths are preserved.
        """
        _PRIORITY_SEGMENTS = ("deliverable", "results")

        def _path_priority(p: str) -> int:
            lowered = p.lower()
            for idx, segment in enumerate(_PRIORITY_SEGMENTS):
                if f"/{segment}/" in lowered or lowered.startswith(f"{segment}/"):
                    return idx
            return len(_PRIORITY_SEGMENTS)

        resolved: List[str] = []
        for raw_path in context_paths:
            text = raw_path.strip()
            if not text:
                resolved.append(raw_path)
                continue
            # Absolute paths: keep as-is
            if text.startswith("/"):
                resolved.append(text)
                continue

            target_basename = os.path.basename(text)
            if not target_basename:
                resolved.append(text)
                continue

            # Collect all matches: list of (dep_id, artifact_path)
            matches: List[Tuple[int, str]] = []
            for dep_id, artifact_paths in dep_artifacts:
                for ap in artifact_paths:
                    if os.path.basename(ap) == target_basename:
                        matches.append((dep_id, ap))

            if not matches:
                resolved.append(text)
                continue

            if len(matches) == 1:
                resolved.append(matches[0][1])
                continue

            # Multiple matches — group by dep_id, pick max dep_id
            max_dep_id = max(dep_id for dep_id, _ in matches)
            same_dep_matches = [ap for dep_id, ap in matches if dep_id == max_dep_id]

            # Cross-dependency conflict: warn
            dep_ids_with_match = sorted(set(dep_id for dep_id, _ in matches))
            if len(dep_ids_with_match) > 1:
                logger.warning(
                    "Multiple dependencies have artifact '%s': deps %s, using dep %d",
                    target_basename,
                    dep_ids_with_match,
                    max_dep_id,
                )

            if len(same_dep_matches) == 1:
                resolved.append(same_dep_matches[0])
                continue

            # Same-dependency internal conflict: pick by path priority.
            # Among candidates with the same priority, take the last one in the
            # artifact list (later entries are typically more final).
            best_priority = min(_path_priority(p) for p in same_dep_matches)
            candidates_at_best = [p for p in same_dep_matches if _path_priority(p) == best_priority]
            best = candidates_at_best[-1]
            resolved.append(best)

        return resolved

    def _derive_skill_tool_hints(
        self,
        *,
        task_text: str,
        dependency_paths: Sequence[str],
        paper_mode: bool,
    ) -> List[str]:
        text = task_text.lower()
        hints: set[str] = set()
        bio_suffixes = (
            ".fasta",
            ".fa",
            ".fna",
            ".faa",
            ".fastq",
            ".fq",
            ".sam",
            ".bam",
            ".gff",
            ".gff3",
        )
        if paper_mode or any(term in text for term in ("paper", "manuscript", "report")):
            hints.add("manuscript_writer")
        if any(path.lower().endswith(bio_suffixes) for path in dependency_paths) or any(
            term in text
            for term in (
                "fasta",
                "fastq",
                "sequence",
                "genome",
                "assembly",
                "alignment",
                "annotation",
                "phage",
            )
        ):
            hints.add("bio_tools")
        return sorted(hints)

    def _build_skill_trace_payload(
        self,
        *,
        selection_result: Any,
        injection_result: Any,
    ) -> Dict[str, Any]:
        return {
            "candidate_skill_ids": list(selection_result.candidate_skill_ids),
            "selected_skill_ids": list(selection_result.selected_skill_ids),
            "selection_source": selection_result.selection_source,
            "injection_mode_by_skill": dict(injection_result.injection_mode_by_skill),
            "injected_chars": int(injection_result.injected_chars),
            "selection_latency_ms": selection_result.selection_latency_ms,
        }

    def _persist_skill_trace(
        self,
        *,
        plan_id: int,
        node: PlanNode,
        skill_trace: Dict[str, Any],
        enabled: bool,
    ) -> None:
        if not enabled:
            return
        merged_metadata = dict(node.metadata or {})
        merged_metadata["skill_trace"] = skill_trace
        try:
            self._repo.update_task(plan_id, node.id, metadata=merged_metadata)
            node.metadata = merged_metadata
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Failed to persist skill trace for plan %s task %s: %s",
                plan_id,
                node.id,
                exc,
            )
        _log_job(
            "info",
            "Skill trace captured",
            {"sub_type": "skill_trace", "task_id": node.id, "skill_trace": skill_trace},
        )

    def _should_use_deep_think(self, config: ExecutionConfig) -> bool:
        _ = config
        return True

    def _run_task_with_deep_think(
        self,
        *,
        plan_id: int,
        node: PlanNode,
        parent: Optional[PlanNode],
        dependencies: List[PlanNode],
        plan_outline: Optional[str],
        tree: PlanTree,
        config: ExecutionConfig,
    ) -> ExecutionResult:
        try:
            self._repo.update_task(plan_id, node.id, status="running", execution_result="")
            node.status = "running"
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Failed to mark task %s as running for plan %s: %s",
                node.id,
                plan_id,
                exc,
            )

        session_context = dict(config.session_context or {})
        node_metadata = node.metadata if isinstance(node.metadata, dict) else {}
        paper_mode = bool(
            config.paper_mode
            or session_context.get("paper_mode")
            or node_metadata.get("paper_mode")
        )
        user_query = (node.instruction or node.display_name() or f"Execute task #{node.id}").strip()
        dep_outputs: List[Dict[str, Any]] = []
        paper_context_paths: List[str] = []
        tool_result_context: Dict[str, Any] = {"artifact_paths": [], "session_artifact_paths": []}
        raw_paper_context_paths = node_metadata.get("paper_context_paths")
        if isinstance(raw_paper_context_paths, list):
            for item in raw_paper_context_paths:
                if isinstance(item, str) and item.strip():
                    paper_context_paths.append(item.strip())
        _artifact_registry = session_context.get("_artifact_registry")
        _artifact_manifest = self._get_artifact_manifest(plan_id, session_context)
        for dep in dependencies:
            artifact_context = self._dependency_artifact_context(
                dep,
                _artifact_registry,
                artifact_manifest=_artifact_manifest,
            )
            artifact_paths = artifact_context.get("artifact_paths") or []
            if isinstance(artifact_paths, list):
                for artifact_path in artifact_paths:
                    if isinstance(artifact_path, str) and artifact_path not in paper_context_paths:
                        paper_context_paths.append(artifact_path)
            dep_outputs.append(
                {
                    "id": dep.id,
                    "name": dep.display_name(),
                    "status": dep.status,
                    "execution_result": self._prompt_builder._summarize_long_result(
                        dep.execution_result or "(not executed)",
                        max_length=4000,
                    ),
                    "artifact_paths": artifact_paths,
                    "deliverable_manifest": artifact_context.get("deliverable_manifest"),
                    "published_modules": artifact_context.get("published_modules"),
                }
            )
        # Resolve relative paper_context_paths against dependency artifacts
        dep_artifacts_for_resolve: List[Tuple[int, List[str]]] = [
            (dep_out["id"], dep_out["artifact_paths"])
            for dep_out in dep_outputs
            if isinstance(dep_out.get("artifact_paths"), list)
        ]
        if dep_artifacts_for_resolve and paper_context_paths:
            paper_context_paths = self._resolve_context_paths_from_deps(
                paper_context_paths, dep_artifacts_for_resolve
            )
        dependency_paths = self._collect_dependency_paths(
            dependencies,
            paper_context_paths,
            artifact_registry=_artifact_registry,
            artifact_manifest=_artifact_manifest,
        )
        task_ancestor_chain, task_work_dir = self._resolve_task_tool_workspace(
            node,
            session_id=session_context.get("session_id"),
            tree=tree,
        )

        if paper_mode:
            session_context["paper_mode"] = True
            if dependency_paths:
                session_context["paper_context_paths"] = dependency_paths[:40]

        constraints = [
            "You are in TASK EXECUTION mode, not conversational chat. Complete the task using tools, then produce the output file/result.",
            "Produce actionable output for this task only.",
            "Honor dependency outputs and do not redo completed dependencies.",
            "Do NOT call submit_final_answer — task completion is determined by producing the required output.",
        ]
        # Extract tool names mentioned in the instruction and enforce priority
        _instruction_lower = (node.instruction or "").lower()
        _tool_names_in_instruction = [
            t for t in [
                "literature_pipeline", "web_search", "code_executor", "bio_tools",
                "sequence_fetch", "document_reader", "vision_reader", "graph_rag",
                "phagescope_research", "phagescope", "deeppl", "manuscript_writer", "review_pack_writer",
                "file_operations", "terminal_session", "deliverable_submit",
            ]
            if t in _instruction_lower
        ]
        if _tool_names_in_instruction:
            constraints.append(
                f"CRITICAL: The task instruction explicitly specifies tool(s): {', '.join(_tool_names_in_instruction)}. "
                f"You MUST use these tool(s) as the primary method. Do NOT substitute with other tools "
                f"(e.g., do NOT use code_executor or manuscript_writer when literature_pipeline is specified)."
            )
        # Inject session runtime directory so LLM knows where files are
        session_id = session_context.get("session_id")
        if session_id:
            runtime_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                "runtime",
                session_id,
            )
            if os.path.isdir(runtime_dir):
                constraints.append(
                    f"Session runtime directory: {runtime_dir} — use it to locate session-scoped inputs. "
                    "Do NOT guess paths like /workspace or /root."
                )
        if task_work_dir:
            constraints.append(
                f"Task output directory: {task_work_dir} — write final task files here. "
                "Prefer relative paths or bare filenames so file_operations resolves into this directory. "
                "Do NOT place final deliverables under session workspace/."
            )
        if paper_mode:
            constraints.extend(
                [
                    "Paper mode is enabled: prioritize dependency artifact_paths and paper_context_paths as primary evidence.",
                    "When resolved_input_artifacts or absolute paper_context_paths are provided, treat them as the canonical source of truth instead of guessing filenames.",
                    "Do not claim completion without explicit citation integrity checks against provided reference files.",
                    "CRITICAL TOOL SELECTION: For writing ANY paper content (sections, drafts, revisions, full assembly), you MUST use manuscript_writer. Do NOT use code_executor to write paper text. code_executor may only be used for data analysis, code generation, or non-writing tasks.",
                    "MD-FIRST MANUSCRIPT RULE: write the manuscript source as Markdown (.md) first and make it pass manuscript quality gates before attempting PDF rendering. PDF is a derived deliverable, not the source of truth.",
                ]
            )
        # --- Skill content injection ---
        skill_context = None
        skill_trace = {
            "candidate_skill_ids": [],
            "selected_skill_ids": [],
            "selection_source": "disabled",
            "injection_mode_by_skill": {},
            "injected_chars": 0,
            "selection_latency_ms": 0.0,
        }
        if config.enable_skills:
            try:
                loader = get_skills_loader(auto_sync=False)
                tool_hints = self._derive_skill_tool_hints(
                    task_text=user_query,
                    dependency_paths=dependency_paths,
                    paper_mode=paper_mode,
                )
                selection_result = self._run_coroutine_sync(
                    loader.select_skills(
                        task_title=node.display_name(),
                        task_description=user_query,
                        llm_service=self._llm._llm,
                        dependency_paths=dependency_paths,
                        tool_hints=tool_hints,
                        preferred_skills=session_context.get("plan_skill_candidates") or [],
                        selection_mode=config.skill_selection_mode,
                        max_skills=config.skill_max_per_task,
                        scope="task",
                    )
                )
                injection_result = loader.build_skill_context(
                    selection_result.selected_skill_ids,
                    max_chars=config.skill_budget_chars,
                )
                skill_context = injection_result.content or None
                skill_trace = self._build_skill_trace_payload(
                    selection_result=selection_result,
                    injection_result=injection_result,
                )
                if skill_context:
                    logger.info(
                        "Injecting %d chars of skill context for task %s",
                        len(skill_context),
                        node.id,
                    )
            except Exception as exc:
                logger.warning("Skill content loading failed (non-blocking): %s", exc)

        self._persist_skill_trace(
            plan_id=plan_id,
            node=node,
            skill_trace=skill_trace,
            enabled=config.skill_trace_enabled,
        )

        task_context = TaskExecutionContext(
            task_id=node.id,
            task_name=node.display_name(),
            task_instruction=user_query,
            dependency_outputs=dep_outputs,
            plan_outline=plan_outline,
            constraints=constraints,
            skill_context=skill_context,
            context_summary=node.context_combined,
            context_sections=list(node.context_sections or []),
            paper_context_paths=dependency_paths[:40],
        )
        reasoning_language = detect_reasoning_language(user_query)

        async def on_thinking(step: ThinkingStep) -> None:
            _log_job(
                "info",
                "DeepThink step update",
                {
                    "sub_type": "thinking_step",
                    "task_id": node.id,
                    "step": build_user_visible_step(
                        step,
                        language=reasoning_language,
                        preserve_thought=True,
                    ),
                },
            )

        async def on_thinking_delta(iteration: int, delta: str) -> None:
            _log_job(
                "info",
                "DeepThink delta update",
                {
                    "sub_type": "thinking_delta",
                    "task_id": node.id,
                    "iteration": iteration,
                    "delta": delta,
                },
            )

        async def on_tool_start(tool: str, params: Dict[str, Any]) -> None:
            _log_job(
                "info",
                f"DeepThink tool start: {tool}",
                {
                    "sub_type": "tool_call_start",
                    "task_id": node.id,
                    "tool": tool,
                    "params": params,
                },
            )

        async def on_tool_result(tool: str, payload: Dict[str, Any]) -> None:
            extracted_context = self._extract_tool_result_context(payload)
            if extracted_context:
                for key in ("artifact_paths", "session_artifact_paths"):
                    values = extracted_context.get(key)
                    if not isinstance(values, list):
                        continue
                    existing = tool_result_context.setdefault(key, [])
                    if not isinstance(existing, list):
                        existing = []
                        tool_result_context[key] = existing
                    for item in values:
                        if isinstance(item, str) and item not in existing:
                            existing.append(item)
                for key in (
                    "run_directory",
                    "working_directory",
                    "task_directory_full",
                    "task_root_directory",
                    "results_directory",
                    "work_dir",
                    "run_dir",
                ):
                    value = extracted_context.get(key)
                    if isinstance(value, str) and value.strip() and not tool_result_context.get(key):
                        tool_result_context[key] = value.strip()
            _log_job(
                "info" if payload.get("success") else "error",
                f"DeepThink tool finished: {tool}",
                {
                    "sub_type": "tool_call_result",
                    "task_id": node.id,
                    "tool": tool,
                    "payload": payload,
                },
            )

        async def _tool_wrapper(tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
            return await self._tool_executor.execute(
                tool_name,
                params,
                context=ToolExecutionContext(
                    plan_id=node.plan_id,
                    task_id=node.id,
                    task_name=node.display_name(),
                    task_instruction=node.instruction,
                    session_id=session_context.get("session_id"),
                    ancestor_chain=task_ancestor_chain,
                    owner_id=session_context.get("owner_id"),
                    current_job_id=self._current_job_id(),
                    work_dir=task_work_dir,
                    channel="plan_executor",
                    mode="task_execution",
                ),
            )

        deep_think_agent = DeepThinkAgent(
            llm_client=self._llm._llm,
            available_tools=[
                "web_search",
                "graph_rag",
                "sequence_fetch",
                "code_executor",
                "file_operations",
                "document_reader",
                "vision_reader",
                "bio_tools",
                "literature_pipeline",
                "review_pack_writer",
                "phagescope_research",
                "phagescope",
                "deeppl",
                "plan_operation",
                "manuscript_writer",
                "terminal_session",
                "deliverable_submit",
            ],
            tool_executor=_tool_wrapper,
            max_iterations=getattr(self._settings, "deep_think_max_iterations", 16),
            tool_timeout=UnifiedToolExecutor.DEFAULT_TIMEOUT_SECONDS,
            on_thinking=on_thinking,
            on_thinking_delta=on_thinking_delta,
            on_tool_start=on_tool_start,
            on_tool_result=on_tool_result,
        )
        current_job_id = self._current_job_id()
        if current_job_id:
            try:
                from .decomposition_jobs import JobRuntimeController, plan_decomposition_jobs

                plan_decomposition_jobs.register_runtime_controller(
                    current_job_id,
                    JobRuntimeController(
                        pause=deep_think_agent.pause,
                        resume=deep_think_agent.resume,
                        skip_step=deep_think_agent.skip_step,
                    ),
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Failed to register runtime controller: %s", exc)

        try:
            result = self._run_coroutine_sync(
                deep_think_agent.think(
                    user_query,
                    context=session_context,
                    task_context=task_context,
                )
            )
            payload = {
                "status": "success",
                "content": result.final_answer,
                "notes": [result.thinking_summary] if result.thinking_summary else [],
                "metadata": {
                    "deep_think": True,
                    "confidence": result.confidence,
                    "tools_used": result.tools_used,
                    "thinking_process": {
                        "status": "completed",
                        "total_iterations": result.total_iterations,
                        "summary": result.thinking_summary,
                        "steps": [
                            build_user_visible_step(
                                step,
                                language=reasoning_language,
                                preserve_thought=True,
                            )
                            for step in result.thinking_steps
                        ],
                    },
                },
            }
            final_answer_paths = self._extract_path_like_values(result.final_answer)
            if final_answer_paths:
                existing_paths = [
                    str(item).strip()
                    for item in list(tool_result_context.get("artifact_paths") or [])
                    if str(item).strip()
                ]
                tool_result_context["artifact_paths"] = list(
                    dict.fromkeys([*final_answer_paths, *existing_paths])
                )[:40]
            for key in (
                "run_directory",
                "working_directory",
                "task_directory_full",
                "task_root_directory",
                "results_directory",
                "work_dir",
                "run_dir",
            ):
                value = tool_result_context.get(key)
                if isinstance(value, str) and value.strip():
                    payload[key] = value.strip()
            artifact_paths = tool_result_context.get("artifact_paths")
            if isinstance(artifact_paths, list) and artifact_paths:
                payload["artifact_paths"] = artifact_paths[:40]
                payload["metadata"]["artifact_paths"] = artifact_paths[:40]
            session_artifact_paths = tool_result_context.get("session_artifact_paths")
            if isinstance(session_artifact_paths, list) and session_artifact_paths:
                payload["session_artifact_paths"] = session_artifact_paths[:40]
                payload["metadata"]["session_artifact_paths"] = session_artifact_paths[:40]
            finalization, _ = self._finalize_task_execution(
                plan_id,
                node,
                payload,
                execution_status="completed",
            )
            finalization, raw_response = self._materialize_finalization(
                plan_id,
                node,
                finalization,
                session_context=config.session_context,
            )
            node.execution_result = raw_response
            node.status = finalization.final_status
            tree.nodes[node.id] = node
            if parent:
                tree.nodes[parent.id] = parent
            return ExecutionResult(
                plan_id=plan_id,
                task_id=node.id,
                status=finalization.final_status,
                content=str(finalization.payload.get("content") or result.final_answer),
                notes=[result.thinking_summary] if result.thinking_summary else [],
                metadata=finalization.payload.get("metadata") or {},
                raw_response=raw_response,
                attempts=1,
            )
        except Exception as exc:
            logger.exception("DeepThink task execution failed for task %s: %s", node.id, exc)
            failure_payload = {
                "status": "failed",
                "content": str(exc),
                "notes": ["deep think execution failed"],
                "metadata": {"deep_think": True},
            }
            finalization, _ = self._finalize_task_execution(
                plan_id,
                node,
                failure_payload,
                execution_status="failed",
            )
            finalization, raw_response = self._materialize_finalization(
                plan_id,
                node,
                finalization,
                session_context=config.session_context,
            )
            node.execution_result = raw_response
            node.status = finalization.final_status
            tree.nodes[node.id] = node
            return ExecutionResult(
                plan_id=plan_id,
                task_id=node.id,
                status=finalization.final_status,
                content=str(exc),
                notes=["deep think execution failed"],
                metadata=finalization.payload.get("metadata") or {"deep_think": True},
                raw_response=raw_response,
                attempts=1,
            )
        finally:
            if current_job_id:
                try:
                    from .decomposition_jobs import plan_decomposition_jobs

                    plan_decomposition_jobs.unregister_runtime_controller(current_job_id)
                except Exception:  # pragma: no cover - defensive
                    pass

    def _run_coroutine_sync(self, coro: Any) -> Any:
        return _run_coroutine_sync(coro)

    def _persist_execution(
        self,
        plan_id: int,
        task_id: int,
        payload: Dict[str, Any],
        *,
        status: Optional[str] = None,
    ) -> None:
        serialized = json.dumps(payload, ensure_ascii=False)
        try:
            self._repo.update_task(
                plan_id,
                task_id,
                execution_result=serialized,
                status=status,
            )
        except Exception:
            logger.exception(
                "Failed to persist execution result for plan %s task %s",
                plan_id,
                task_id,
            )
            raise

        # When a task completes successfully, clear stale "skipped" status on
        # downstream tasks that were blocked by this task's previous failure.
        # Without this, re-executing a failed task leaves its dependents stuck
        # in "skipped" even though the dependency is now satisfied.
        if status in ("completed", "done", "success"):
            self._clear_stale_skipped_dependents(plan_id, task_id)

    def _clear_stale_skipped_dependents(
        self,
        plan_id: int,
        completed_task_id: int,
    ) -> None:
        """Reset downstream tasks stuck in 'skipped' because this task previously failed.

        When a task is re-executed and succeeds, any direct dependents that were
        skipped due to ``blocked_by_dependencies`` should be reset to ``pending``
        so they can be re-executed.
        """
        try:
            tree = self._repo.get_plan_tree(plan_id)
        except Exception:
            return

        for node in tree.nodes.values():
            if str(getattr(node, "status", "") or "").strip().lower() != "skipped":
                continue
            deps = getattr(node, "dependencies", []) or []
            if completed_task_id not in [int(d) for d in deps if str(d).strip().isdigit()]:
                continue

            # Confirm it was blocked by dependencies (not skipped for other reasons)
            exec_result = getattr(node, "execution_result", None)
            if isinstance(exec_result, str):
                try:
                    exec_data = json.loads(exec_result)
                except (json.JSONDecodeError, TypeError):
                    exec_data = {}
            else:
                exec_data = exec_result if isinstance(exec_result, dict) else {}
            meta = exec_data.get("metadata", {}) if isinstance(exec_data, dict) else {}
            if not (isinstance(meta, dict) and meta.get("blocked_by_dependencies")):
                continue

            try:
                self._repo.update_task(plan_id, node.id, status="pending", execution_result="")
                logger.info(
                    "[TASK_RECOVERY] Reset skipped task %s to pending "
                    "(dependency task %s now completed, plan %s)",
                    node.id,
                    completed_task_id,
                    plan_id,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to reset skipped task %s for plan %s: %s",
                    node.id,
                    plan_id,
                    exc,
                )

    def _promote_workspace_artifacts_to_task_dir(
        self,
        *,
        node: PlanNode,
        payload: Dict[str, Any],
        session_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not isinstance(payload, dict) or not isinstance(session_context, dict):
            return payload

        session_id = str(session_context.get("session_id") or "").strip()
        if not session_id:
            return payload

        try:
            from app.services.session_paths import get_runtime_session_dir

            session_dir = get_runtime_session_dir(session_id, create=True).resolve()
        except Exception:
            return payload

        workspace_dir = (session_dir / "workspace").resolve()
        try:
            _ancestor_chain, task_dir_str = self._resolve_task_tool_workspace(
                node,
                session_id=session_id,
            )
            task_dir = Path(task_dir_str).resolve()
        except Exception:
            return payload

        candidate_paths = self._extract_path_like_values(payload)
        if not candidate_paths:
            return payload

        promoted_abs_paths: List[str] = []
        promoted_session_paths: List[str] = []

        for candidate in candidate_paths:
            try:
                source = Path(str(candidate)).expanduser().resolve(strict=False)
            except Exception:
                continue
            if not source.exists() or not source.is_file():
                continue

            try:
                source.relative_to(task_dir)
                promoted_abs_paths.append(str(source))
                try:
                    promoted_session_paths.append(
                        str(source.relative_to(session_dir)).replace("\\", "/")
                    )
                except Exception:
                    pass
                continue
            except ValueError:
                pass

            try:
                rel_workspace = source.relative_to(workspace_dir)
            except ValueError:
                continue

            target = (task_dir / rel_workspace).resolve()
            try:
                target.relative_to(task_dir)
            except ValueError:
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(source, target)
            except Exception as exc:
                logger.warning(
                    "Failed to promote workspace artifact %s into %s: %s",
                    source,
                    target,
                    exc,
                )
                continue

            promoted_abs_paths.append(str(target))
            try:
                promoted_session_paths.append(
                    str(target.relative_to(session_dir)).replace("\\", "/")
                )
            except Exception:
                pass

        if not promoted_abs_paths and not promoted_session_paths:
            return payload

        normalized_payload = dict(payload)

        existing_artifact_paths = [
            str(item).strip()
            for item in list(normalized_payload.get("artifact_paths") or [])
            if str(item).strip()
        ]
        if promoted_abs_paths:
            normalized_payload["artifact_paths"] = list(
                dict.fromkeys([*existing_artifact_paths, *promoted_abs_paths])
            )[:40]
            normalized_payload["produced_files"] = list(normalized_payload["artifact_paths"])

        existing_session_paths = [
            str(item).strip()
            for item in list(normalized_payload.get("session_artifact_paths") or [])
            if str(item).strip()
        ]
        if promoted_session_paths:
            normalized_payload["session_artifact_paths"] = list(
                dict.fromkeys([*existing_session_paths, *promoted_session_paths])
            )[:40]

        metadata = normalized_payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            normalized_payload["metadata"] = metadata
        if isinstance(normalized_payload.get("artifact_paths"), list):
            metadata["artifact_paths"] = list(normalized_payload["artifact_paths"])
        if isinstance(normalized_payload.get("session_artifact_paths"), list):
            metadata["session_artifact_paths"] = list(
                normalized_payload["session_artifact_paths"]
            )

        return normalized_payload

    def _materialize_finalization(
        self,
        plan_id: int,
        node: PlanNode,
        finalization: VerificationFinalization,
        *,
        session_context: Optional[Dict[str, Any]],
    ) -> Tuple[VerificationFinalization, str]:
        finalization.payload = self._promote_workspace_artifacts_to_task_dir(
            node=node,
            payload=finalization.payload,
            session_context=session_context,
        )
        finalization.payload = self._enrich_finalized_payload_with_artifacts(
            plan_id=plan_id,
            node=node,
            payload=finalization.payload,
            final_status=finalization.final_status,
            session_context=session_context,
        )
        finalization = self._task_verifier.apply_artifact_authority(
            plan_id,
            node,
            finalization,
            manifest=self._get_artifact_manifest(plan_id, session_context),
        )
        raw_response = json.dumps(finalization.payload, ensure_ascii=False)
        self._persist_execution(
            plan_id,
            node.id,
            finalization.payload,
            status=finalization.final_status,
        )
        return finalization, raw_response

    def _finalize_task_execution(
        self,
        plan_id: int,
        node: PlanNode,
        payload: Dict[str, Any],
        *,
        execution_status: Optional[str],
        trigger: str = "auto",
    ) -> Tuple[VerificationFinalization, str]:
        finalization = self._task_verifier.finalize_payload(
            node,
            payload,
            execution_status=execution_status,
            trigger=trigger,
        )
        serialized = json.dumps(finalization.payload, ensure_ascii=False)
        return finalization, serialized

    def _execution_order(self, tree: PlanTree) -> Iterable[PlanNode]:
        ordered: List[PlanNode] = []
        emitted: set[int] = set()
        visiting: set[int] = set()
        scheduled: set[int] = set()
        stack: List[Tuple[int, int]] = []

        def schedule(node_id: int, *, force: bool = False) -> None:
            if node_id not in tree.nodes:
                return
            if node_id in emitted:
                return
            if not force and node_id in scheduled:
                return
            stack.append((node_id, 0))
            scheduled.add(node_id)

        def process_stack() -> None:
            while stack:
                current_id, stage = stack.pop()

                if current_id not in tree.nodes:
                    continue

                if stage == 0:
                    if current_id in emitted:
                        continue
                    if current_id in visiting:
                        logger.warning(
                            "Detected circular dependency while ordering task %s, skipping",
                            current_id
                        )
                        continue
                    visiting.add(current_id)
                    stack.append((current_id, 1))
                    node = tree.nodes[current_id]
                    for dep_id in reversed(node.dependencies):
                        if dep_id not in tree.nodes or dep_id in emitted:
                            continue
                        if dep_id in visiting:
                            logger.warning(
                                "Detected circular dependency between tasks %s and %s, skipping dependency",
                                current_id, dep_id
                            )
                            continue
                        if dep_id in scheduled:
                            continue
                        stack.append((dep_id, 0))
                        scheduled.add(dep_id)
                    continue

                if stage == 1:
                    node = tree.nodes[current_id]
                    stack.append((current_id, 2))
                    child_ids = list(tree.children_ids(current_id))
                    for child_id in reversed(child_ids):
                        if child_id not in tree.nodes or child_id in emitted:
                            continue
                        if child_id in visiting:
                            logger.warning(
                                "Detected circular dependency between parent %s and child %s, skipping",
                                current_id, child_id
                            )
                            continue
                        if child_id in scheduled:
                            continue
                        stack.append((child_id, 0))
                        scheduled.add(child_id)
                    continue

                if stage == 2:
                    visiting.discard(current_id)
                    if current_id in emitted:
                        continue
                    emitted.add(current_id)
                    ordered.append(tree.nodes[current_id])

        for root_id in tree.root_node_ids():
            schedule(root_id)

        process_stack()

        for node_id in tree.nodes.keys():
            if node_id not in emitted:
                schedule(node_id, force=True)
                process_stack()

        return ordered

    def _resolve_dependencies(
        self,
        tree: PlanTree,
        node: PlanNode,
    ) -> List[PlanNode]:
        deps: List[PlanNode] = []
        for dep_id in node.dependencies:
            dep = tree.nodes.get(dep_id)
            if dep is not None:
                deps.append(dep)
        return deps

    def _get_artifact_manifest(
        self,
        plan_id: int,
        session_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        manifest = None
        if isinstance(session_context, dict):
            manifest = session_context.get("_artifact_manifest")
        if isinstance(manifest, dict) and int(manifest.get("plan_id") or plan_id) == plan_id:
            manifest.setdefault("artifacts", {})
            return manifest
        manifest = load_artifact_manifest(plan_id)
        if isinstance(session_context, dict):
            session_context["_artifact_manifest"] = manifest
        return manifest

    def _save_artifact_manifest(
        self,
        plan_id: int,
        manifest: Dict[str, Any],
        session_context: Optional[Dict[str, Any]],
    ) -> None:
        save_artifact_manifest(plan_id, manifest)
        if isinstance(session_context, dict):
            session_context["_artifact_manifest"] = manifest

    def _resolve_task_artifact_contract(self, node: PlanNode) -> Dict[str, List[str]]:
        metadata = node.metadata if isinstance(node.metadata, dict) else {}
        return infer_artifact_contract(
            task_name=node.display_name(),
            instruction=node.instruction or "",
            metadata=metadata,
        )

    def _task_can_publish_artifacts(
        self,
        plan_id: int,
        node: PlanNode,
        *,
        tree: Optional[PlanTree] = None,
        state_by_task: Optional[Dict[int, Dict[str, Any]]] = None,
        session_context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if state_by_task is None and tree is not None:
            state_by_task = self._status_resolver.resolve_plan_states(
                plan_id,
                tree,
                manifest=self._get_artifact_manifest(plan_id, session_context),
            )
        if isinstance(state_by_task, dict):
            effective_status = str(
                (state_by_task.get(node.id) or {}).get("effective_status") or ""
            ).strip().lower()
            if effective_status:
                return effective_status == "completed"

        raw_status = str(getattr(node, "status", "") or "").strip().lower()
        if raw_status in {"done", "success"}:
            raw_status = "completed"
        if raw_status != "completed":
            return False

        payload: Any = getattr(node, "execution_result", None)
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {"content": payload}

        payload_status = ""
        metadata: Dict[str, Any] = {}
        if isinstance(payload, dict):
            payload_status = str(payload.get("status", "") or "").strip().lower()
            if payload_status in {"done", "success"}:
                payload_status = "completed"
            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}

        if payload_status and payload_status != "completed":
            return False
        if bool(metadata.get("blocked_by_dependencies")):
            return False
        return True

    def _extract_candidate_artifact_paths(self, node: PlanNode) -> List[str]:
        payload: Any = node.execution_result
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {"content": payload}
        candidates = self._extract_path_like_values(payload)
        if isinstance(payload, dict):
            metadata = payload.get("metadata")
            if isinstance(metadata, dict):
                published = metadata.get("published_artifacts")
                if isinstance(published, dict):
                    for entry in published.values():
                        if not isinstance(entry, dict):
                            continue
                        for key in ("path", "source_path"):
                            value = str(entry.get(key) or "").strip()
                            if value and value not in candidates:
                                candidates.append(value)
        return candidates[:80]

    def _backfill_task_artifacts(
        self,
        plan_id: int,
        node: PlanNode,
        manifest: Dict[str, Any],
        session_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        if node.id <= 0:
            return {}
        metadata = node.metadata if isinstance(node.metadata, dict) else {}
        provenance = resolve_artifact_contract_with_provenance(
            task_name=node.display_name(),
            instruction=node.instruction or "",
            metadata=metadata,
        )
        candidate_paths = self._extract_candidate_artifact_paths(node)
        backfill_enabled = bool(
            getattr(self._settings, "artifact_backfill_enabled", False)
        )
        if backfill_enabled:
            # Compatibility path: extend the contract with runtime-discovered
            # aliases so legacy plans without explicit publishes still land
            # their artifacts in the canonical manifest.
            extended = extend_contract_with_runtime_candidates(
                provenance,
                task_name=node.display_name(),
                instruction=node.instruction or "",
                candidate_paths=candidate_paths,
            )
            publishes_to_backfill = extended.publishes()
            if not provenance.has_explicit and publishes_to_backfill:
                logger.info(
                    "[ARTIFACT_BACKFILL_COMPAT] plan=%s task=%s inferred_publishes=%s "
                    "runtime_publishes=%s — legacy compatibility path used.",
                    plan_id,
                    node.id,
                    extended.inferred_publishes,
                    extended.runtime_publishes,
                )
        else:
            # Default path: honor explicit publishes when present. For older
            # plans (or tasks that persisted an empty artifact_contract block),
            # fall back to acceptance/instruction-derived publish aliases so
            # canonical artifacts still land in the manifest.
            publishes_to_backfill = list(provenance.explicit_publishes)
            if not publishes_to_backfill:
                publishes_to_backfill = list(provenance.inferred_publishes)

        published: Dict[str, Dict[str, Any]] = {}
        manifest_changed = False
        artifact_registry = None
        if isinstance(session_context, dict):
            artifact_registry = session_context.get("_artifact_registry")

        for alias in publishes_to_backfill:
            existing_entry = manifest.get("artifacts", {}).get(alias) if isinstance(manifest.get("artifacts"), dict) else None
            source = find_candidate_source_for_alias(alias=alias, candidate_paths=candidate_paths)
            if source is None and backfill_enabled:
                runtime_candidates = find_runtime_candidates(plan_id, node.id, alias)
                for runtime_path in runtime_candidates:
                    if runtime_path not in candidate_paths:
                        candidate_paths.append(runtime_path)
                source = find_candidate_source_for_alias(alias=alias, candidate_paths=candidate_paths)
            if source is None and isinstance(existing_entry, dict):
                existing = resolve_manifest_aliases(manifest, [alias]).get(alias)
                if existing:
                    published[alias] = dict(existing_entry)
                    continue
            if source is None:
                continue
            entry = publish_artifact(
                plan_id=plan_id,
                alias=alias,
                source_path=source,
                producer_task_id=node.id,
                manifest=manifest,
            )
            if entry is None:
                continue
            manifest_changed = True
            published[alias] = entry
            if isinstance(artifact_registry, dict):
                existing_paths = artifact_registry.setdefault(node.id, [])
                if entry["path"] not in existing_paths:
                    existing_paths.append(entry["path"])

        if manifest_changed:
            self._save_artifact_manifest(plan_id, manifest, session_context)
        return published

    def _resolve_required_artifacts(
        self,
        plan_id: int,
        node: PlanNode,
        *,
        dependencies: List[PlanNode],
        tree: PlanTree,
        session_context: Optional[Dict[str, Any]],
    ) -> Tuple[Dict[str, List[str]], Dict[str, str], List[str], Dict[str, List[int]]]:
        contract = self._resolve_task_artifact_contract(node)
        required_aliases = list(contract.get("requires") or [])
        metadata = node.metadata if isinstance(node.metadata, dict) else {}
        explicit_contract = metadata.get("artifact_contract") if isinstance(metadata.get("artifact_contract"), dict) else {}
        explicit_required_aliases = [
            str(alias).strip()
            for alias in list(explicit_contract.get("requires") or [])
            if str(alias).strip()
        ]
        if not required_aliases:
            return contract, {}, [], {}

        manifest = self._get_artifact_manifest(plan_id, session_context)

        backfill_enabled = bool(
            getattr(self._settings, "artifact_backfill_enabled", False)
        )

        # Only backfill dependency artifacts when the legacy compat flag is on.
        # In authority mode (default), dependencies must have already published
        # their artifacts during their own execution via _materialize_finalization.
        if backfill_enabled:
            state_by_task = self._status_resolver.resolve_plan_states(
                plan_id,
                tree,
                manifest=manifest,
            )
            for dep in dependencies:
                if self._task_can_publish_artifacts(
                    plan_id,
                    dep,
                    state_by_task=state_by_task,
                ):
                    self._backfill_task_artifacts(plan_id, dep, manifest, session_context)

        resolved = resolve_manifest_aliases(manifest, required_aliases)
        missing_for_resolution = [alias for alias in required_aliases if alias not in resolved]
        missing = [alias for alias in explicit_required_aliases if alias not in resolved]
        if not missing_for_resolution:
            return contract, resolved, [], {}

        # Producer scan: only attempt backfill when compat flag is on
        producer_map: Dict[str, List[int]] = {}
        all_nodes = list(tree.nodes.values())
        for alias in missing_for_resolution:
            producer_map[alias] = producer_candidates_for_alias(alias, all_nodes)
            if backfill_enabled:
                for producer_id in producer_map[alias]:
                    producer = tree.nodes.get(producer_id)
                    if producer is None or not self._task_can_publish_artifacts(
                        plan_id,
                        producer,
                        state_by_task=state_by_task,
                    ):
                        continue
                    self._backfill_task_artifacts(plan_id, producer, manifest, session_context)
                refreshed = resolve_manifest_aliases(manifest, [alias]).get(alias)
                if refreshed:
                    resolved[alias] = refreshed

        final_missing = [alias for alias in explicit_required_aliases if alias not in resolved]
        return contract, resolved, final_missing, producer_map

    def _block_for_missing_artifacts(
        self,
        *,
        plan_id: int,
        node: PlanNode,
        tree: PlanTree,
        missing_aliases: List[str],
        producer_candidates: Dict[str, List[int]],
        resolved_input_artifacts: Dict[str, str],
    ) -> ExecutionResult:
        blocking_ids: List[int] = []
        for alias in missing_aliases:
            blocking_ids.extend(producer_candidates.get(alias) or [])
        unique_blocking_ids = sorted({task_id for task_id in blocking_ids if task_id != node.id})
        dependency_info = []
        for task_id in unique_blocking_ids:
            dep = tree.nodes.get(task_id)
            if dep is None:
                continue
            dependency_info.append(
                {"id": dep.id, "name": dep.display_name(), "status": dep.status}
            )
        reason = (
            f"Blocked by dependencies: task #{node.id} is missing required published artifacts "
            f"{missing_aliases}. Resolve upstream producer task(s) first."
        )
        notes = [
            "This task was not executed because required input artifacts are missing.",
            f"Missing artifact aliases: {', '.join(missing_aliases)}",
        ]
        metadata = {
            "blocked_by_dependencies": True,
            "missing_artifact_aliases": list(missing_aliases),
            "producer_task_candidates": producer_candidates,
            "resolved_input_artifacts": dict(resolved_input_artifacts),
            "incomplete_dependencies": unique_blocking_ids,
            "incomplete_dependency_info": dependency_info,
        }
        payload = {
            "status": "skipped",
            "content": reason,
            "notes": notes,
            "metadata": metadata,
        }
        finalization = self._task_verifier.finalize_payload(
            node,
            payload,
            execution_status="skipped",
        )
        raw_response = json.dumps(finalization.payload, ensure_ascii=False)
        self._persist_execution(
            plan_id,
            node.id,
            finalization.payload,
            status=finalization.final_status,
        )
        node.status = finalization.final_status
        node.execution_result = raw_response
        tree.nodes[node.id] = node
        return ExecutionResult(
            plan_id=plan_id,
            task_id=node.id,
            status=finalization.final_status,
            content=reason,
            notes=notes,
            metadata=finalization.payload.get("metadata") or {},
            raw_response=raw_response,
        )

    def _enrich_finalized_payload_with_artifacts(
        self,
        *,
        plan_id: int,
        node: PlanNode,
        payload: Dict[str, Any],
        final_status: str,
        session_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        metadata = payload.setdefault("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
            payload["metadata"] = metadata

        resolved_inputs = {}
        if isinstance(session_context, dict):
            maybe_resolved = session_context.get("resolved_input_artifacts")
            if isinstance(maybe_resolved, dict):
                resolved_inputs = {
                    str(alias): str(path)
                    for alias, path in maybe_resolved.items()
                    if str(alias).strip() and str(path).strip()
                }
        if resolved_inputs:
            metadata["resolved_input_artifacts"] = resolved_inputs

        contract = self._resolve_task_artifact_contract(node)
        if contract.get("requires") or contract.get("publishes"):
            metadata["artifact_contract"] = contract

        if final_status != "completed":
            return payload

        manifest = self._get_artifact_manifest(plan_id, session_context)
        payload_for_scan = json.loads(json.dumps(payload, ensure_ascii=False))
        temp_node = node.model_copy(
            update={
                "execution_result": json.dumps(payload_for_scan, ensure_ascii=False),
            }
        )
        published = self._backfill_task_artifacts(plan_id, temp_node, manifest, session_context)
        if published:
            metadata["published_artifacts"] = published
            metadata["artifact_manifest_path"] = str(artifact_manifest_path(plan_id))
            self._save_artifact_manifest(plan_id, manifest, session_context)
        return payload

    @staticmethod
    def _is_internal_artifact_path(value: str) -> bool:
        normalized = "/" + str(value or "").strip().replace("\\", "/").lstrip("/")
        if not normalized or normalized == "/":
            return False
        lowered = normalized.lower()
        basename = lowered.rsplit("/", 1)[-1]
        if basename in _INTERNAL_ARTIFACT_FILENAMES and "/tool_outputs/" in lowered:
            return True
        return bool(_INTERNAL_TOOL_OUTPUT_RE.search(lowered))

    @staticmethod
    def _is_non_deliverable_workspace_path(value: str) -> bool:
        normalized = "/" + str(value or "").strip().replace("\\", "/").lstrip("/")
        if not normalized or normalized == "/":
            return False
        return bool(_NON_DELIVERABLE_WORKSPACE_RE.search(normalized))

    @classmethod
    def _extract_path_like_values(cls, payload: Any) -> List[str]:
        if payload is None:
            return []
        path_keys = {
            "path",
            "output_path",
            "analysis_path",
            "effective_output_path",
            "effective_analysis_path",
            "partial_output_path",
            "combined_path",
            "combined_partial",
            "sections_dir",
            "reviews_dir",
            "merge_queue",
            "citation_validation_path",
            "manifest_path",
            "result_path",
            "preview_path",
            "references_bib",
            "evidence_md",
            "library_jsonl",
            "pdf_dir",
            "artifact_paths",
            "produced_files",
            "session_artifact_paths",
        }
        found: List[str] = []
        seen: set[str] = set()

        def _add(value: Any) -> None:
            if not isinstance(value, str):
                return
            text = value.strip()
            if not text or text in seen:
                return
            if "\n" in text or "\r" in text:
                return
            if "/" not in text and "." not in text:
                return
            if (
                cls._is_internal_artifact_path(text)
                or cls._is_non_deliverable_workspace_path(text)
                or _is_non_canonical_runtime_path(text)
            ):
                return
            seen.add(text)
            found.append(text)

        def _visit(value: Any, key: Optional[str] = None) -> None:
            if value is None:
                return
            if isinstance(value, dict):
                for item_key, item_value in value.items():
                    lowered = str(item_key).strip().lower()
                    if lowered in {"artifact_paths", "produced_files", "session_artifact_paths"} and isinstance(
                        item_value, (list, tuple, set)
                    ):
                        for item in item_value:
                            _add(item)
                    elif lowered in path_keys or lowered.endswith("_path") or lowered.endswith("_file") or lowered.endswith("_dir"):
                        if isinstance(item_value, (list, tuple, set)):
                            for item in item_value:
                                _add(item)
                        else:
                            _add(item_value)
                    if isinstance(item_value, (dict, list, tuple, set)):
                        _visit(item_value, key=lowered)
                return
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    _visit(item, key=key)
                return
            if isinstance(value, str) and key:
                if key in path_keys or key.endswith("_path") or key.endswith("_file") or key.endswith("_dir"):
                    _add(value)
            elif isinstance(value, str):
                for match in _PATH_LIKE_RE.finditer(value):
                    _add(match.group(1))

        _visit(payload)
        return found[:40]

    @classmethod
    def _extract_tool_result_context(cls, payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        result = payload.get("result")
        if not isinstance(result, dict):
            return {}

        extracted: Dict[str, Any] = {}
        artifact_paths = cls._extract_path_like_values(result)
        if artifact_paths:
            extracted["artifact_paths"] = artifact_paths[:40]

        session_artifact_paths = result.get("session_artifact_paths")
        if isinstance(session_artifact_paths, list):
            cleaned_session_paths: List[str] = []
            for item in session_artifact_paths:
                text = str(item or "").strip()
                if (
                    not text
                    or cls._is_internal_artifact_path(text)
                    or cls._is_non_deliverable_workspace_path(text)
                    or _is_non_canonical_runtime_path(text)
                ):
                    continue
                if text not in cleaned_session_paths:
                    cleaned_session_paths.append(text)
            if cleaned_session_paths:
                extracted["session_artifact_paths"] = cleaned_session_paths[:40]

        for key in (
            "run_directory",
            "working_directory",
            "task_directory_full",
            "task_root_directory",
            "results_directory",
            "work_dir",
            "run_dir",
        ):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                extracted[key] = value.strip()

        metadata = result.get("metadata")
        if isinstance(metadata, dict):
            for key in (
                "run_directory",
                "working_directory",
                "task_directory_full",
                "task_root_directory",
                "results_directory",
                "work_dir",
                "run_dir",
            ):
                value = metadata.get(key)
                if isinstance(value, str) and value.strip() and key not in extracted:
                    extracted[key] = value.strip()

        return extracted

    def _dependency_artifact_context(
        self,
        dep: PlanNode,
        artifact_registry: Optional[Dict[int, List[str]]] = None,
        artifact_manifest: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        # --- Priority 1: artifact_registry (accumulated across tasks) ---
        registry_paths = (artifact_registry or {}).get(dep.id)
        manifest_paths = published_artifact_paths_for_task(artifact_manifest or {}, dep.id)
        if isinstance(registry_paths, list) and registry_paths:
            combined_paths: List[str] = []
            for candidate in list(registry_paths) + list(manifest_paths):
                if isinstance(candidate, str) and candidate and candidate not in combined_paths:
                    combined_paths.append(candidate)
            return {
                "artifact_paths": combined_paths[:40],
                "deliverable_manifest": None,
                "published_modules": [],
            }
        if manifest_paths:
            return {
                "artifact_paths": manifest_paths[:40],
                "deliverable_manifest": None,
                "published_modules": [],
            }

        # --- Priority 2: parse from execution_result ---
        if not dep.execution_result:
            return {"artifact_paths": [], "deliverable_manifest": None}
        payload: Any = dep.execution_result
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {"content": payload}
        if not isinstance(payload, dict):
            return {"artifact_paths": [], "deliverable_manifest": None}

        deliverables = payload.get("metadata", {}).get("deliverables") if isinstance(payload.get("metadata"), dict) else None
        if not isinstance(deliverables, dict):
            deliverables = {}
        manifest_path = deliverables.get("manifest_path")
        if not isinstance(manifest_path, str):
            manifest_path = None

        artifact_paths = self._extract_path_like_values(payload)
        if manifest_path and manifest_path not in artifact_paths:
            artifact_paths.insert(0, manifest_path)
        return {
            "artifact_paths": artifact_paths[:40],
            "deliverable_manifest": manifest_path,
            "published_modules": deliverables.get("published_modules") if isinstance(deliverables.get("published_modules"), list) else [],
        }

    def _generate_plan_summary(
        self,
        plan_id: int,
        tree: PlanTree,
        summary: "ExecutionSummary",
        config: ExecutionConfig,
    ) -> Optional[str]:
        """Generate a comprehensive summary of the plan execution.

        Collects all completed task results and uses LLM to synthesize
        a final report.

        Returns:
            Summary text or None if generation fails
        """
        completed_results = []
        for result in summary.results:
            if result.status == "completed":
                node = tree.nodes.get(result.task_id)
                if node:
                    completed_results.append({
                        "task_id": result.task_id,
                        "task_name": node.display_name(),
                        "instruction": node.instruction,
                        "result": result.content[:2000] if len(result.content) > 2000 else result.content,
                    })

        if not completed_results:
            return None

        summary_prompt = (
            "You are generating a comprehensive execution summary for a completed plan.\n\n"
            f"Plan Title: {tree.title}\n"
            f"Plan Description: {tree.description or 'N/A'}\n\n"
            "=== COMPLETED TASKS ===\n"
        )

        for r in completed_results:
            summary_prompt += f"\n**Task {r['task_id']}: {r['task_name']}**\n"
            if r['instruction']:
                summary_prompt += f"Instruction: {r['instruction'][:200]}...\n" if len(r['instruction'] or '') > 200 else f"Instruction: {r['instruction']}\n"
            summary_prompt += f"Result: {r['result']}\n"

        summary_prompt += (
            "\n=== YOUR TASK ===\n"
            "Generate a concise executive summary (200-400 words) that:\n"
            "1. Summarizes what was accomplished across all tasks\n"
            "2. Highlights key findings, outputs, or deliverables\n"
            "3. Notes any important files or artifacts created\n"
            "4. Identifies any issues or areas needing follow-up\n\n"
            "Write the summary in a professional, clear style."
        )

        try:
            response = self._llm.generate(
                summary_prompt,
                config,
            )
            return response.content
        except Exception as exc:
            raise RuntimeError("LLM plan summary generation failed.") from exc

    def _execute_tool_call(
        self,
        tool_call: ToolCallRequest,
        node: PlanNode,
        config: ExecutionConfig,
    ) -> Dict[str, Any]:
        """Execute a tool call requested by the executor LLM.

        Args:
            tool_call: The tool call request from LLM
            node: The current task node being executed
            config: Execution configuration

        Returns:
            Dict with success status, result/error, and summary
        """
        tool_name = tool_call.name
        params = dict(tool_call.parameters)
        session_id = None
        owner_id = None
        if config.session_context:
            maybe_session = config.session_context.get("session_id")
            if isinstance(maybe_session, str) and maybe_session.strip():
                session_id = maybe_session.strip()
            maybe_owner = config.session_context.get("owner_id")
            if isinstance(maybe_owner, str) and maybe_owner.strip():
                owner_id = maybe_owner.strip()

        logger.info(
            "PlanExecutor executing tool %s for task %s with params: %s",
            tool_name, node.id, list(params.keys())
        )
        _log_job(
            "info",
            f"Executing tool {tool_name} for task {node.id}",
            {"tool": tool_name, "task_id": node.id},
        )

        try:
            _ancestor_chain_sync, task_work_dir = self._resolve_task_tool_workspace(
                node,
                session_id=session_id,
            )
            payload = self._tool_executor.execute_sync(
                tool_name,
                params,
                context=ToolExecutionContext(
                    plan_id=node.plan_id,
                    task_id=node.id,
                    task_name=node.display_name(),
                    task_instruction=node.instruction,
                    session_id=session_id,
                    ancestor_chain=_ancestor_chain_sync,
                    owner_id=owner_id,
                    current_job_id=self._current_job_id(),
                    work_dir=task_work_dir,
                    channel="plan_executor",
                    mode="task_execution",
                ),
            )
            logger.info(
                "Tool %s execution succeeded for task %s",
                tool_name, node.id
            )
            return payload

        except Exception as exc:
            logger.exception(
                "Tool %s execution failed for task %s: %s",
                tool_name, node.id, exc
            )
            _log_job(
                "error",
                f"Tool {tool_name} failed: {exc}",
                {"tool": tool_name, "task_id": node.id, "error": str(exc)},
            )
            return {"success": False, "error": str(exc)}

    @staticmethod
    def _clip_tool_text(value: Any, *, limit: int = 320) -> str:
        if value is None:
            return ""
        text = " ".join(str(value).split()).strip()
        if not text:
            return ""
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)] + "..."

    def _build_tool_failure_error(self, tool_name: str, result: Any) -> str:
        if not isinstance(result, dict):
            return f"{tool_name} failed: Tool execution returned success=false."

        direct_error = self._clip_tool_text(
            result.get("error") or result.get("message"),
            limit=600,
        )
        if direct_error:
            return direct_error

        parts: List[str] = []
        exit_code = result.get("exit_code")
        if exit_code is not None:
            parts.append(f"exit_code={exit_code}")

        blocked_reason = self._clip_tool_text(result.get("blocked_reason"), limit=200)
        if blocked_reason:
            parts.append(f"blocked_reason={blocked_reason}")

        stderr = self._clip_tool_text(result.get("stderr"), limit=320)
        if stderr:
            parts.append(f"stderr={stderr}")

        stdout = self._clip_tool_text(result.get("stdout"), limit=220)
        if stdout:
            parts.append(f"stdout={stdout}")

        nested_result = result.get("result")
        if isinstance(nested_result, dict):
            nested_error = self._clip_tool_text(
                nested_result.get("error") or nested_result.get("message"),
                limit=600,
            )
            if nested_error:
                parts.append(f"detail={nested_error}")

        if not parts:
            return "Tool execution returned success=false."
        return f"{tool_name} failed: {'; '.join(parts)}"

    def _summarize_tool_result(self, tool_name: str, result: Any) -> str:
        """Generate a brief summary of tool execution result."""
        if result is None:
            return "(no result)"

        if tool_name == "phagescope" and isinstance(result, dict):
            action = str(result.get("action") or "phagescope").strip().lower()
            if result.get("success") is False:
                return f"PhageScope {action} failed: {result.get('error') or result.get('message') or 'unknown error'}"
            if action == "submit":
                taskid = result.get("taskid")
                if taskid is None and isinstance(result.get("data"), dict):
                    taskid = result["data"].get("taskid")
                return f"PhageScope submit succeeded: taskid={taskid}; running in background."
            if action == "task_detail":
                status = "unknown"
                data = result.get("data")
                if isinstance(data, dict):
                    parsed = data.get("parsed_task_detail")
                    if isinstance(parsed, dict):
                        status = str(parsed.get("task_status") or status)
                    results = data.get("results")
                    if isinstance(results, dict):
                        status = str(results.get("status") or status)
                return f"PhageScope task_detail succeeded: status={status}."
            if action == "save_all":
                out_dir = result.get("output_directory") or result.get("output_directory_rel")
                if out_dir:
                    return f"PhageScope save_all completed: {out_dir}"
                return "PhageScope save_all completed."
            if action == "batch_submit":
                if result.get("success") is False:
                    return f"PhageScope batch_submit failed: {result.get('error') or 'unknown error'}"
                return (
                    f"PhageScope batch_submit: batch_id={result.get('batch_id')}; "
                    f"primary_taskid={result.get('primary_taskid')}; manifest={result.get('manifest_path')}."
                )
            if action == "batch_reconcile":
                if result.get("success") is False:
                    return f"PhageScope batch_reconcile failed: {result.get('error') or 'unknown error'}"
                miss = result.get("missing_phage_ids") or []
                n = len(miss) if isinstance(miss, list) else 0
                return f"PhageScope batch_reconcile: batch_id={result.get('batch_id')}; missing_count={n}."
            if action == "batch_retry":
                if result.get("success") is False:
                    return f"PhageScope batch_retry failed: {result.get('error') or 'unknown error'}"
                return f"PhageScope batch_retry: batch_id={result.get('batch_id')}."
            return f"PhageScope {action} succeeded."

        if tool_name == "deeppl" and isinstance(result, dict):
            action = str(result.get("action") or "deeppl").strip().lower()
            if result.get("success") is False:
                return f"DeepPL {action} failed: {result.get('error') or result.get('message') or 'unknown error'}"
            if action == "predict":
                label = result.get("predicted_label") or "unknown"
                lifestyle = result.get("predicted_lifestyle") or "unknown"
                fraction = result.get("positive_window_fraction")
                if isinstance(fraction, (int, float)):
                    return (
                        f"DeepPL predict succeeded: label={label}, lifestyle={lifestyle}, "
                        f"positive_window_fraction={fraction:.4f}."
                    )
                return f"DeepPL predict succeeded: label={label}, lifestyle={lifestyle}."
            if action == "job_status":
                return f"DeepPL job_status succeeded: status={result.get('status') or 'unknown'}."
            return f"DeepPL {action} succeeded."

        if isinstance(result, dict):
            if "summary" in result:
                return str(result["summary"])[:1000]
            if "result" in result:
                return str(result["result"])[:1000]
            if "output" in result:
                return str(result["output"])[:1000]
            import json
            try:
                return json.dumps(result, ensure_ascii=False)[:1000]
            except (TypeError, ValueError):
                return str(result)[:1000]

        return str(result)[:1000]

    def _current_job_id(self) -> Optional[str]:
        try:
            from .decomposition_jobs import get_current_job

            return get_current_job()
        except Exception:  # pragma: no cover - defensive
            return None


    @staticmethod
    def _normalize_status(raw: str) -> str:
        normalized = (raw or "").strip().lower()
        mapping = {
            "success": "completed",
            "failed": "failed",
            "failure": "failed",
            "skipped": "skipped",
            "complete": "completed",
            "completed": "completed",
        }
        if normalized in mapping:
            return mapping[normalized]
        if not normalized:
            return "completed"
        return normalized


__all__ = [
    "ExecutionConfig",
    "ExecutionResponse",
    "ExecutionResult",
    "ExecutionSummary",
    "ExecutorPromptBuilder",
    "PlanExecutor",
    "PlanExecutorLLMService",
]
