from __future__ import annotations

import json
import logging
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field, ValidationError

from ...config.executor_config import ExecutorSettings, get_executor_settings
from ...llm import LLMClient
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
from .plan_models import PlanNode, PlanTree
from .task_verification import TaskVerificationService, VerificationFinalization

logger = logging.getLogger(__name__)

def _log_job(level: str, message: str, metadata: Optional[Dict[str, Any]] = None) -> None:
    try:
        from .decomposition_jobs import log_job_event
    except Exception:  # pragma: no cover - defensive
        return
    log_job_event(level, message, metadata)

if TYPE_CHECKING:  # pragma: no cover
    from ...repository.plan_repository import PlanRepository


# ---------------------------------------------------------------------------
# Pydantic models for structured LLM responses
# ---------------------------------------------------------------------------


class ToolCallRequest(BaseModel):
    """Tool call request from executor LLM."""
    name: str = Field(description="Tool name: claude_code, web_search, document_reader, etc.")
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
    enable_skills: bool = True
    skill_budget_chars: int = 6000
    skill_selection_mode: str = "hybrid"
    skill_max_per_task: int = 3
    skill_trace_enabled: bool = True

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
        "You can either produce text-based results directly OR request tool execution when needed. "
        "Respond using the JSON schema provided."
    )

    TOOL_CATALOG = """
=== AVAILABLE TOOLS ===
When a task requires tool execution, request one of these tools:

**BIOINFORMATICS (FASTA/FASTQ/sequences):**
- sequence_fetch: Deterministic accession-to-FASTA download with strict allowlist
  Parameters: {"accession": "<id>" | "accessions": ["<id1>", "<id2>"], "database": "nuccore|protein", "format": "fasta", optional "session_id"/"output_name"}
  NOTE: Use this first when the user asks to download FASTA by accession IDs.
- bio_tools: Execute bioinformatics Docker tools (seqkit, blast, prodigal, hmmer, checkv, etc.)
  Parameters: {"tool_name": "seqkit|blast|prodigal|...", "operation": "stats|blastn|predict|help", "input_file": "<path>", "sequence_text": "<inline FASTA/raw sequence>", "params": {...}}
  NOTE: Always call operation="help" first if unsure about parameters!
  NOTE: If no file is provided but sequence text is available, pass it via sequence_text.
  NOTE: If unsure which tool/operation should be used for the requested analysis, request web_search first with a focused query, then return a bio_tools call.

**CODE EXECUTION & DATA ANALYSIS:**
- claude_code: Execute one concrete implementation task (data analysis, visualization, model building)
  Parameters: {"task": "<atomic implementation instruction only>", "allowed_tools": "Bash,Edit"}
  IMPORTANT: Never ask claude_code to do planning/decomposition/roadmap work.
  IMPORTANT: Default to direct objective execution. Do NOT request standalone CLI preflight
  checks (for example version/install/environment diagnostics) unless the user explicitly asks
  for diagnostics or a prior execution has already failed due to environment/tooling issues.

**INFORMATION RETRIEVAL:**
- web_search: Search the web for information
  Parameters: {"query": "<search query>", "max_results": 5}
- graph_rag: Query phage-host knowledge graph
  Parameters: {"query": "<query>", "top_k": 5, "hops": 2}

**FILE & DOCUMENT PROCESSING:**
- document_reader: Read and extract content from text files (PDF, TXT, DOCX)
  Parameters: {"operation": "read_any", "file_path": "<path>"}
- vision_reader: OCR and visual understanding for images/scanned PDFs
  Parameters: {"operation": "ocr_page|describe_figure|read_equation_image", "file_path": "<path>"}

**SPECIALIZED:**
- phagescope: PhageScope platform operations (submit jobs, check status, fetch results)
  Parameters: {"action": "submit|task_list|task_detail|task_log|result|quality|download|save_all|batch_submit|batch_reconcile|batch_retry", optional "session_id", "phage_ids", "batch_id", "strategy", ...}
  IMPORTANT: submit is async and should return taskid quickly. For long-running jobs, prefer submit now and check status later.
- deeppl: DeepPL lifecycle prediction (DNABERT-based)
  Parameters: {"action": "help|predict|job_status", for predict exactly one of "input_file" or "sequence_text", optional "execution_mode", "remote_profile", "model_path", "background", "job_id", "session_id"}
  remote_profile options: gpu | cpu | default
- literature_pipeline: Build a literature evidence pack from PubMed/PMC
  Parameters: {"query": "<query>", optional "max_results", "download_pdfs", "max_pdfs", "session_id"}
- review_pack_writer: Generate a literature-backed review draft
  Parameters: {"topic": "<topic>", optional "query", "max_results", "download_pdfs", "sections", "max_revisions", "evaluation_threshold", "session_id"}
- manuscript_writer: Generate research manuscripts
  Parameters: {"task": "<writing task>", "output_path": "<path>"}

**DELIVERABLES (session sidebar / paper bundle):**
- deliverable_submit: Promote specific files into the session Deliverables tree (code, image_tabular, paper, refs, docs).
  Parameters: {"publish": true|false, "artifacts": [{"path": "<file>", "module": "code|image_tabular|paper|refs|docs", optional "reason": "<note>"}]}
  When DELIVERABLES_INGEST_MODE=explicit, tool outputs (e.g. claude_code) are NOT auto-mirrored into Deliverables; call this after the user wants figures/code included for submission.
  Do not call for browse-only reads unless the user asks to publish to Deliverables.
"""

    TASK_TYPE_GUIDANCE = """
=== WHEN TO USE TOOLS ===
- For design/architecture/planning/text writing → respond with text only (status: "success")
- For accession-based FASTA download → use sequence_fetch first (status: "needs_tool")
- For FASTA/FASTQ/sequence analysis → use bio_tools FIRST (status: "needs_tool")
- If the user provides sequence text instead of a file, call bio_tools with sequence_text.
- If bioinformatics tool/operation routing is uncertain, use web_search first to disambiguate, then call bio_tools (status: "needs_tool")
- Do not use claude_code as fallback for bio_tools input conversion/parsing failures.
- Do not use claude_code as fallback for sequence_fetch download/input failures.
- For data analysis/visualization/charts → use claude_code (status: "needs_tool")
- For model code building/training → use claude_code (status: "needs_tool")
- Never use claude_code for task planning or decomposition; planning stays in the orchestration layer.
- For claude_code coding tasks, request direct implementation/execution first; avoid standalone
  "check CLI/version/environment" steps unless diagnostics are explicitly requested or needed after a failure.
- For web information lookup → use web_search (status: "needs_tool")
- For reading text files (PDF/TXT) → use document_reader (status: "needs_tool")
- For reading images/scanned docs → use vision_reader (status: "needs_tool")
- For phage knowledge queries → use graph_rag (status: "needs_tool")
- For PhageScope long-running analyses → submit first, do not wait in the same turn; report taskid and current background status.
- For lifecycle prediction with DeepPL → use deeppl action=predict and include explicit label/confidence in the response.
- For literature evidence collection only → use literature_pipeline.
- For a literature-backed review/survey draft → use review_pack_writer.
- For publishing selected outputs to the session Deliverables panel under explicit ingest → use deliverable_submit after files exist and the user wants them in the submission bundle.
"""

    OUTPUT_SCHEMA = """{
  "status": "success" | "failed" | "skipped" | "needs_tool",
  "content": "<main result text or reasoning for tool request>",
  "tool_call": {  // REQUIRED when status is "needs_tool", otherwise omit
    "name": "sequence_fetch" | "bio_tools" | "claude_code" | "web_search" | "document_reader" | "vision_reader" | "graph_rag" | "phagescope" | "deeppl" | "literature_pipeline" | "review_pack_writer" | "manuscript_writer" | "deliverable_submit",
    "parameters": { <tool-specific parameters> }
  },
  "notes": ["optional notes"],
  "metadata": {}
}"""

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
        if paper_mode:
            lines.append("\n=== PAPER MODE ===")
            if paper_section:
                lines.append(f"- paper_section: {paper_section}")
            if paper_role:
                lines.append(f"- paper_role: {paper_role}")
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

        lines.append(self.TOOL_CATALOG)
        lines.append(self.TASK_TYPE_GUIDANCE)

        lines.append("\n=== RESPONSE FORMAT ===")
        lines.append(self.OUTPUT_SCHEMA)
        lines.append("\nOnly return valid JSON, without Markdown or explanations.")
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
                if end_idx == -1:
                    end_idx = prompt.find(self.TOOL_CATALOG[:30], idx)
                if end_idx != -1:
                    prompt = prompt[:idx] + "\n[Plan outline omitted due to prompt size limit]\n" + prompt[end_idx:]
            # Hard-truncate if still too long.
            if len(prompt) > self.MAX_PROMPT_CHARS:
                prompt = prompt[: self.MAX_PROMPT_CHARS] + "\n... [TRUNCATED]\nOnly return valid JSON."
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

    def generate(self, prompt: str, config: ExecutionConfig) -> ExecutionResponse:
        kwargs: Dict[str, Any] = {}
        model = config.model or self._settings.model
        if model:
            kwargs["model"] = model
        if config.timeout is not None:
            kwargs["timeout"] = config.timeout
        response_text = self._llm.chat(prompt, **kwargs)
        cleaned = _strip_code_fences(response_text)
        try:
            return ExecutionResponse.model_validate_json(cleaned)
        except ValidationError:
            logger.error("Failed to parse execution response: %s", cleaned)
            raise


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

    def execute_plan(
        self,
        plan_id: int,
        *,
        config: Optional[ExecutionConfig] = None,
    ) -> ExecutionSummary:
        cfg = config or ExecutionConfig.from_settings(self._settings)
        summary = ExecutionSummary(plan_id=plan_id)
        tree = self._repo.get_plan_tree(plan_id)
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

        for node in order:
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
            summary.results.append(result)

            if result.status == "completed":
                summary.executed_task_ids.append(node.id)
            elif result.status == "skipped":
                summary.skipped_task_ids.append(node.id)
            else:
                summary.failed_task_ids.append(node.id)
                if cfg.dependency_throttle:
                    logger.warning(
                        "Stopping execution for plan %s due to failure on task %s",
                        plan_id,
                        node.id,
                    )
                    _log_job(
                        "warning",
                        "Plan execution stopped early because a task failed.",
                        {"plan_id": plan_id, "failed_task_id": node.id},
                    )
                    break

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
                    "duration_sec": result.duration_sec,
                },
            )

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
            self._repo.update_task(plan_id, node.id, status="running")
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
                response = self._llm.generate(prompt, config)

                if response.status == "needs_tool" and response.tool_call:
                    _log_job(
                        "info",
                        f"Task {node.id} requests tool: {response.tool_call.name}",
                        {
                            "plan_id": plan_id,
                            "task_id": node.id,
                            "tool": response.tool_call.name,
                        },
                    )
                    tool_result = self._execute_tool_call(
                        response.tool_call,
                        node=node,
                        config=config,
                    )
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
                raw_response = json.dumps(result_payload, ensure_ascii=False)
                task_status = self._normalize_status(response.status)
                finalization, raw_response = self._finalize_task_execution(
                    plan_id,
                    node,
                    result_payload,
                    execution_status=task_status,
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
                continue

        error_message = str(last_error) if last_error else "Unknown execution failure"
        finalization, raw_response = self._finalize_task_execution(
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
    ) -> List[str]:
        seen: set[str] = set()
        ordered: List[str] = []
        for path in paper_context_paths:
            text = str(path).strip()
            if text and text not in seen:
                seen.add(text)
                ordered.append(text)
        for dep in dependencies:
            artifact_context = self._dependency_artifact_context(dep)
            artifact_paths = artifact_context.get("artifact_paths") or []
            if not isinstance(artifact_paths, list):
                continue
            for artifact_path in artifact_paths:
                text = str(artifact_path).strip()
                if text and text not in seen:
                    seen.add(text)
                    ordered.append(text)
        return ordered

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
            self._repo.update_task(plan_id, node.id, status="running")
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
        raw_paper_context_paths = node_metadata.get("paper_context_paths")
        if isinstance(raw_paper_context_paths, list):
            for item in raw_paper_context_paths:
                if isinstance(item, str) and item.strip():
                    paper_context_paths.append(item.strip())
        for dep in dependencies:
            artifact_context = self._dependency_artifact_context(dep)
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
                        max_length=1200,
                    ),
                    "artifact_paths": artifact_paths,
                    "deliverable_manifest": artifact_context.get("deliverable_manifest"),
                    "published_modules": artifact_context.get("published_modules"),
                }
            )
        dependency_paths = self._collect_dependency_paths(
            dependencies,
            paper_context_paths,
        )

        if paper_mode:
            session_context["paper_mode"] = True
            if dependency_paths:
                session_context["paper_context_paths"] = dependency_paths[:40]

        constraints = [
            "Produce actionable output for this task only.",
            "Honor dependency outputs and do not redo completed dependencies.",
        ]
        if paper_mode:
            constraints.extend(
                [
                    "Paper mode is enabled: prioritize dependency artifact_paths and paper_context_paths as primary evidence.",
                    "Do not claim completion without explicit citation integrity checks against provided reference files.",
                    "CRITICAL TOOL SELECTION: For writing ANY paper content (sections, drafts, revisions, full assembly), you MUST use manuscript_writer. Do NOT use claude_code to write paper text. claude_code may only be used for data analysis, code generation, or non-writing tasks.",
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
                    current_job_id=self._current_job_id(),
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
                "claude_code",
                "file_operations",
                "document_reader",
                "vision_reader",
                "bio_tools",
                "literature_pipeline",
                "review_pack_writer",
                "phagescope",
                "deeppl",
                "plan_operation",
                "manuscript_writer",
                "terminal_session",
                "deliverable_submit",
            ],
            tool_executor=_tool_wrapper,
            max_iterations=12,
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
            finalization, raw_response = self._finalize_task_execution(
                plan_id,
                node,
                payload,
                execution_status="completed",
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
            finalization, raw_response = self._finalize_task_execution(
                plan_id,
                node,
                failure_payload,
                execution_status="failed",
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
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop and running_loop.is_running():
            with ThreadPoolExecutor(max_workers=1) as executor:
                return executor.submit(lambda: asyncio.run(coro)).result()
        return asyncio.run(coro)

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
        self._persist_execution(
            plan_id,
            node.id,
            finalization.payload,
            status=finalization.final_status,
        )
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

    @staticmethod
    def _extract_path_like_values(payload: Any) -> List[str]:
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
            seen.add(text)
            found.append(text)

        def _visit(value: Any, key: Optional[str] = None) -> None:
            if value is None:
                return
            if isinstance(value, dict):
                for item_key, item_value in value.items():
                    lowered = str(item_key).strip().lower()
                    if lowered in path_keys or lowered.endswith("_path") or lowered.endswith("_file") or lowered.endswith("_dir"):
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

        _visit(payload)
        return found[:40]

    def _dependency_artifact_context(self, dep: PlanNode) -> Dict[str, Any]:
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
        if config.session_context:
            maybe_session = config.session_context.get("session_id")
            if isinstance(maybe_session, str) and maybe_session.strip():
                session_id = maybe_session.strip()

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
            payload = self._tool_executor.execute_sync(
                tool_name,
                params,
                context=ToolExecutionContext(
                    plan_id=node.plan_id,
                    task_id=node.id,
                    task_name=node.display_name(),
                    task_instruction=node.instruction,
                    session_id=session_id,
                    current_job_id=self._current_job_id(),
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
