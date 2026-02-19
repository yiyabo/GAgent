from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError

from ...config.executor_config import ExecutorSettings, get_executor_settings
from ...llm import LLMClient
from ..deliverables import get_deliverable_publisher
from ..llm.llm_service import LLMService
from .plan_models import PlanNode, PlanTree

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
            enforce_dependencies=getattr(settings, 'enforce_dependencies', True),
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
- bio_tools: Execute bioinformatics Docker tools (seqkit, blast, prodigal, hmmer, checkv, etc.)
  Parameters: {"tool_name": "seqkit|blast|prodigal|...", "operation": "stats|blastn|predict|help", "input_file": "<path>", "params": {...}}
  NOTE: Always call operation="help" first if unsure about parameters!

**CODE EXECUTION & DATA ANALYSIS:**
- claude_code: Execute one concrete implementation task (data analysis, visualization, model building)
  Parameters: {"task": "<atomic implementation instruction only>", "allowed_tools": "Bash,Edit"}
  IMPORTANT: Never ask claude_code to do planning/decomposition/roadmap work.

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
  Parameters: {"action": "submit|task_list|task_detail|task_log|result|quality|download|save_all", optional "session_id", ...}
  IMPORTANT: submit is async and should return taskid quickly. For long-running jobs, prefer submit now and check status later.
- manuscript_writer: Generate research manuscripts
  Parameters: {"task": "<writing task>", "output_path": "<path>"}
"""

    TASK_TYPE_GUIDANCE = """
=== WHEN TO USE TOOLS ===
- For design/architecture/planning/text writing → respond with text only (status: "success")
- For FASTA/FASTQ/sequence analysis → use bio_tools FIRST (status: "needs_tool")
- For data analysis/visualization/charts → use claude_code (status: "needs_tool")
- For model code building/training → use claude_code (status: "needs_tool")
- Never use claude_code for task planning or decomposition; planning stays in the orchestration layer.
- For web information lookup → use web_search (status: "needs_tool")
- For reading text files (PDF/TXT) → use document_reader (status: "needs_tool")
- For reading images/scanned docs → use vision_reader (status: "needs_tool")
- For phage knowledge queries → use graph_rag (status: "needs_tool")
- For PhageScope long-running analyses → submit first, do not wait in the same turn; report taskid and current background status.
"""

    OUTPUT_SCHEMA = """{
  "status": "success" | "failed" | "skipped" | "needs_tool",
  "content": "<main result text or reasoning for tool request>",
  "tool_call": {  // REQUIRED when status is "needs_tool", otherwise omit
    "name": "bio_tools" | "claude_code" | "web_search" | "document_reader" | "vision_reader" | "graph_rag" | "phagescope" | "manuscript_writer",
    "parameters": { <tool-specific parameters> }
  },
  "notes": ["optional notes"],
  "metadata": {}
}"""

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
        return "\n".join(lines)


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
            raw_response = json.dumps(payload, ensure_ascii=False)

            # Persist skip reason so the UI can render why it was skipped.
            try:
                self._persist_execution(
                    plan_id,
                    node.id,
                    payload,
                    status="skipped",
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
            node.status = "skipped"
            node.execution_result = raw_response
            tree.nodes[node.id] = node

            return ExecutionResult(
                plan_id=plan_id,
                task_id=node.id,
                status="skipped",
                content=skip_reason,
                notes=notes,
                metadata=metadata,
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
                self._persist_execution(
                    plan_id,
                    node.id,
                    result_payload,
                    status=task_status,
                )
                # Update in-memory tree so subsequent tasks see latest outputs.
                node.execution_result = raw_response
                node.status = task_status
                tree.nodes[node.id] = node
                if parent:
                    tree.nodes[parent.id] = parent
                return ExecutionResult(
                    plan_id=plan_id,
                    task_id=node.id,
                    status=task_status,
                    content=response.content,
                    notes=response.notes,
                    metadata=response.metadata,
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
        self._persist_execution(
            plan_id,
            node.id,
            {
                "status": "failed",
                "content": error_message,
                "notes": ["all attempts failed"],
                "metadata": {},
            },
            status="failed",
        )
        node.status = "failed"
        tree.nodes[node.id] = node
        result = ExecutionResult(
            plan_id=plan_id,
            task_id=node.id,
            status="failed",
            content=error_message,
            notes=["all attempts failed"],
            metadata={},
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
        import asyncio
        from concurrent.futures import ThreadPoolExecutor
        from tool_box import execute_tool

        tool_name = tool_call.name
        params = dict(tool_call.parameters)

        if tool_name == "claude_code":
            for key in (
                "require_task_context",
                "skip_permissions",
                "output_format",
                "model",
                "setting_sources",
                "auth_mode",
            ):
                params.pop(key, None)
            legacy_target_task_id = params.pop("target_task_id", None)
            if params.get("task_id") is None:
                params["task_id"] = (
                    legacy_target_task_id
                    if legacy_target_task_id is not None
                    else node.id
                )
            if params.get("plan_id") is None:
                params["plan_id"] = node.plan_id
            params["require_task_context"] = True
            params["auth_mode"] = "api_env"
            params["setting_sources"] = "project"
        else:
            params.pop("target_task_id", None)

        if config.session_context:
            session_id = config.session_context.get("session_id")
            if session_id:
                params["session_id"] = session_id

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
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None

            if running_loop and running_loop.is_running():
                def _run_in_worker() -> Any:
                    return asyncio.run(execute_tool(tool_name, **params))

                with ThreadPoolExecutor(max_workers=1) as executor:
                    result = executor.submit(_run_in_worker).result()
            else:
                result = asyncio.run(execute_tool(tool_name, **params))

            summary = self._summarize_tool_result(tool_name, result)
            tool_success = not (
                isinstance(result, dict)
                and result.get("success") is False
            )
            publish_report = None
            session_id = None
            if config.session_context:
                maybe_session = config.session_context.get("session_id")
                if isinstance(maybe_session, str) and maybe_session.strip():
                    session_id = maybe_session.strip()

            if session_id:
                try:
                    publish_report = self._deliverable_publisher.publish_from_tool_result(
                        session_id=session_id,
                        tool_name=tool_name,
                        raw_result=result,
                        summary=summary,
                        source={
                            "channel": "plan_executor",
                            "mode": "task_execution",
                        },
                        job_id=self._current_job_id(),
                        plan_id=node.plan_id,
                        task_id=node.id,
                        task_name=node.display_name(),
                        task_instruction=node.instruction,
                        publish_status="final" if tool_success else "draft",
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning(
                        "Failed to publish deliverables for plan %s task %s: %s",
                        node.plan_id,
                        node.id,
                        exc,
                    )

            logger.info(
                "Tool %s execution succeeded for task %s",
                tool_name, node.id
            )
            payload: Dict[str, Any] = {
                "success": tool_success,
                "result": result,
                "summary": summary,
            }
            if not tool_success:
                if isinstance(result, dict):
                    payload["error"] = result.get("error") or result.get("message") or "Tool execution returned success=false."
                else:
                    payload["error"] = "Tool execution returned success=false."
            if publish_report is not None:
                payload["deliverables"] = publish_report.to_dict()
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
            return f"PhageScope {action} succeeded."

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
