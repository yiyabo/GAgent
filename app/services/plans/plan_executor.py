from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError

from ...config.executor_config import ExecutorSettings, get_executor_settings
from ...llm import LLMClient
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


class ExecutionResponse(BaseModel):
    """Structured payload returned by the execution LLM."""

    status: str = Field(pattern="^(success|failed|skipped)$")
    content: str
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
        )


# ---------------------------------------------------------------------------
# Prompt builder and LLM façade
# ---------------------------------------------------------------------------


class ExecutorPromptBuilder:
    """Compose prompts for the execution LLM."""

    SYSTEM_HEADER = (
        "You are an execution agent that completes research or engineering tasks. "
        "Produce the requested work product and respond using the JSON schema provided."
    )

    OUTPUT_SCHEMA = (
        "{\n"
        '  "status": "success" | "failed" | "skipped",\n'
        '  "content": "<main execution result text>",\n'
        '  "notes": ["optional notes"],\n'
        '  "metadata": {"model": "executor-llm", "duration_sec": 0}\n'
        "}"
    )

    def build(
        self,
        *,
        node: PlanNode,
        parent: Optional[PlanNode],
        dependencies: List[PlanNode],
        plan_outline: Optional[str],
        include_context: bool,
    ) -> str:
        lines: List[str] = [self.SYSTEM_HEADER]
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
                lines.append(f"Parent Latest Result: {parent.execution_result}")

        if dependencies:
            lines.append("\n=== DEPENDENCIES ===")
            for dep in dependencies:
                summary = dep.execution_result or "(not executed)"
                lines.append(f"- [{dep.id}] {dep.display_name()}: {summary}")

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
        outline = tree.to_outline(max_depth=3, max_nodes=40) if config.include_plan_outline else None
        prompt = self._prompt_builder.build(
            node=node,
            parent=parent,
            dependencies=dependencies,
            plan_outline=outline,
            include_context=config.use_context,
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
                        raise ValueError(
                            f"Detected circular dependency while ordering task {current_id}"
                        )
                    visiting.add(current_id)
                    stack.append((current_id, 1))
                    node = tree.nodes[current_id]
                    for dep_id in reversed(node.dependencies):
                        if dep_id not in tree.nodes or dep_id in emitted:
                            continue
                        if dep_id in visiting:
                            raise ValueError(
                                f"Detected circular dependency between tasks {current_id} and {dep_id}"
                            )
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
                            raise ValueError(
                                f"Detected circular dependency between parent {current_id} and child {child_id}"
                            )
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
