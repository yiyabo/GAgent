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
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, cast

from pydantic import BaseModel, Field, ValidationError

from ...config.executor_config import ExecutorSettings, get_executor_settings
from ...llm import LLMClient, NativeStreamResult, close_current_loop_async_client
from ..tool_schemas import build_executor_tool_schemas, EXECUTOR_AVAILABLE_TOOLS
from app.services.resources.resource_registry import resolve_resources
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
    aliases_for_file_name,
    aliases_for_path_text,
    artifact_manifest_path,
    canonical_artifact_path,
    canonicalize_artifact_alias,
    extend_contract_with_runtime_candidates,
    find_candidate_source_for_alias,
    find_runtime_candidates,
    infer_artifact_contract,
    infer_artifact_namespace,
    load_artifact_manifest,
    producer_candidates_for_alias,
    publish_artifact,
    published_artifact_paths_for_task,
    resolve_artifact_contract_with_provenance,
    resolve_manifest_aliases,
    save_artifact_manifest,
)
from .artifact_preflight import ArtifactPreflightService
from .dependency_validation import (
    normalize_dependencies_for_task,
    normalize_plan_dependencies,
)
from .plan_models import PlanNode, PlanTree
from .status_resolver import PlanStatusResolver
from .task_delegate_executor import CodeAgentTaskDelegateExecutor, TaskDelegationSpec
from .task_verification import TaskVerificationService, VerificationFinalization
from .executor_artifacts import _ArtifactMethods
from .executor_llm import PlanExecutorLLMService
from .executor_models import (
    ExecutionConfig,
    ExecutionResponse,
    ExecutionResult,
    ExecutionSummary,
    ToolCallRequest,
)
from .executor_prompts import ExecutorPromptBuilder, _strip_code_fences
from .executor_text_utils import (
    _BLOCKED_DEPENDENCY_MARKER_RE,
    _LEGACY_SESSION_WORKSPACE_RE,
    _NON_DELIVERABLE_WORKSPACE_RE,
    _PATH_LIKE_RE,
    _PRIMARY_EXECUTION_TOOLS,
    _USELESS_RUNTIME_ROOT_RE,
    _coerce_blocked_dependency_payload,
    _deep_think_has_failed_primary_execution_tool,
    _extract_blocked_dependency_detail,
    _extract_paths_from_execution_result,
    _has_recoverable_output_evidence,
    _is_non_canonical_runtime_path,
    _log_job,
    _path_points_to_recoverable_output,
    _run_coroutine_sync,
    _summarize_tool_params,
)

logger = logging.getLogger(__name__)


if TYPE_CHECKING:  # pragma: no cover
    from ...repository.plan_repository import PlanRepository


# ---------------------------------------------------------------------------
# Main executor façade
# ---------------------------------------------------------------------------


class PlanExecutor(_ArtifactMethods):
    """Execute plan tasks using a dedicated LLM."""

    def __init__(
        self,
        *,
        repo: Optional["PlanRepository"] = None,
        llm_service: Optional[PlanExecutorLLMService] = None,
        settings: Optional[ExecutorSettings] = None,
        prompt_builder: Optional[ExecutorPromptBuilder] = None,
        task_delegate_executor: Optional[CodeAgentTaskDelegateExecutor] = None,
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
        self._task_delegate_executor = task_delegate_executor or CodeAgentTaskDelegateExecutor()

    def _ensure_runtime_helpers(self) -> None:
        if not hasattr(self, "_settings"):
            self._settings = get_executor_settings()
        if not hasattr(self, "_artifact_preflight"):
            self._artifact_preflight = ArtifactPreflightService()
        if not hasattr(self, "_status_resolver"):
            self._status_resolver = PlanStatusResolver()
        if not hasattr(self, "_task_verifier"):
            self._task_verifier = TaskVerificationService()
        if not hasattr(self, "_task_delegate_executor"):
            self._task_delegate_executor = CodeAgentTaskDelegateExecutor()

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
        tree = self._normalize_plan_dependency_edges(tree)
        tree = self._infer_missing_dependencies(tree)
        tree = self._normalize_plan_dependency_edges(tree)
        
        # Use structure-based ordering (post-order traversal) instead of dependency-based
        from app.services.plans.todo_list import build_full_plan_todo_list
        todo = build_full_plan_todo_list(tree, expand_composites=True, ordering_mode="structure")
        order = [tree.nodes[task_id] for task_id in todo.execution_order if task_id in tree.nodes]

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
        _failed_or_skipped: set[int] = set()
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
        tree = self._normalize_plan_dependency_edges(tree)
        tree = self._infer_missing_dependencies(tree)
        tree = self._normalize_plan_dependency_edges(tree)
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
        from app.llm import clear_usage_context, set_usage_context
        session_id = None
        if isinstance(config.session_context, dict):
            session_id = config.session_context.get("session_id")
        usage_token = set_usage_context(
            session_id=session_id,
            plan_id=plan_id,
            task_id=node.id,
            call_purpose="plan_task_execution",
            phase="plan",
            run_id=f"plan_{plan_id}_task_{node.id}",
        )
        try:

            parent = tree.nodes.get(node.parent_id) if node.parent_id else None
            dependencies = self._resolve_dependencies(tree, node)
            plan_state_by_task = self._status_resolver.resolve_plan_states(
                plan_id,
                tree,
                manifest=self._get_artifact_manifest(plan_id, config.session_context),
            )

            incomplete_deps = []
            dep_status_by_id: Dict[int, str] = {}
            for dep in dependencies:
                dep_state = plan_state_by_task.get(dep.id) or {}
                dep_status = str(dep_state.get("effective_status") or dep.status or "pending").strip().lower()
                dep_status_by_id[dep.id] = dep_status
                if dep_status != "completed":
                    incomplete_deps.append(dep)
                    logger.warning(
                        "Dependency %s (status=%s) not completed before executing task %s",
                        dep.id, dep_status, node.id
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
            delegation_mode = self._should_delegate_plan_task(config)
            if missing_aliases and config.enforce_dependencies and not delegation_mode:
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
            elif missing_aliases:
                _log_job(
                    "warning",
                    f"Task {node.id} continuing with missing required artifacts",
                    {
                        "task_id": node.id,
                        "missing_artifact_aliases": missing_aliases,
                        "producer_task_candidates": producer_map,
                        "enforce_dependencies": False,
                    },
                )
                session_context["dependency_warning"] = True
                session_context["degraded_input"] = True
                session_context["missing_artifact_aliases"] = list(missing_aliases)
            if incomplete_deps and config.enforce_dependencies:
                incomplete_ids = [d.id for d in incomplete_deps]
                incomplete_display = ", ".join(
                    f"#{d.id}({dep_status_by_id.get(d.id) or (d.status or 'pending').strip()})" for d in incomplete_deps
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
                session_context["dependency_warning"] = True
                session_context["degraded_input"] = True
                session_context["incomplete_dependencies"] = [d.id for d in incomplete_deps]

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
            resolved_resources: Dict[str, Dict[str, Any]] = {}
            missing_resources: List[str] = []
            resource_ids = list(artifact_contract.get("resources") or []) if isinstance(artifact_contract, dict) else []
            if resource_ids:
                resolved_resources, missing_resources = resolve_resources(resource_ids)
            if session_context is not None:
                session_context["resolved_input_artifacts"] = dict(resolved_input_artifacts)
                session_context["resolved_resources"] = dict(resolved_resources)
                session_context["required_resources"] = list(resource_ids)
            if missing_resources:
                _log_job(
                    "warning",
                    f"Task {node.id} blocked: required external resources unavailable",
                    {
                        "task_id": node.id,
                        "missing_resources": missing_resources,
                        "required_resources": resource_ids,
                    },
                )
                return self._block_for_missing_resources(
                    plan_id=plan_id,
                    node=node,
                    tree=tree,
                    missing_resources=missing_resources,
                    resolved_resources=resolved_resources,
                )
            outline = tree.to_outline(max_depth=4, max_nodes=80) if config.include_plan_outline else None

            if self._should_delegate_plan_task(config):
                return self._run_task_with_external_delegate(
                    plan_id=plan_id,
                    node=node,
                    parent=parent,
                    dependencies=dependencies,
                    plan_outline=outline,
                    tree=tree,
                    config=config,
                    artifact_contract=artifact_contract,
                    resolved_input_artifacts=resolved_input_artifacts,
                    resolved_resources=resolved_resources,
                )

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
                    metadata = result_payload.get("metadata")
                    if not isinstance(metadata, dict):
                        metadata = {}
                        result_payload["metadata"] = metadata
                    if session_context.get("dependency_warning"):
                        metadata["dependency_warning"] = True
                        metadata["degraded_input"] = True
                    incomplete_dependencies = session_context.get("incomplete_dependencies")
                    if isinstance(incomplete_dependencies, list) and incomplete_dependencies:
                        metadata["incomplete_dependencies"] = list(incomplete_dependencies)
                    missing_artifact_aliases = session_context.get("missing_artifact_aliases")
                    if isinstance(missing_artifact_aliases, list) and missing_artifact_aliases:
                        metadata["missing_artifact_aliases"] = list(missing_artifact_aliases)
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

        finally:
            clear_usage_context(usage_token)

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
                if tree.children_ids(node.id):
                    continue
                metadata = node.metadata if isinstance(node.metadata, dict) else {}
                contract = self._resolve_task_artifact_contract(node)
                for alias in contract.get("publishes", []):
                    producer_all.setdefault(alias, [])
                    if node.id not in producer_all[alias]:
                        producer_all[alias].append(node.id)
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
                    producer_all.setdefault(basename, [])
                    if node.id not in producer_all[basename]:
                        producer_all[basename].append(node.id)
                    # Take task_id with the largest value (latest created)
                    if basename not in producer_map or node.id > producer_map[basename]:
                        producer_map[basename] = node.id

            # Log warnings for multi-producer basenames / aliases
            ambiguous_keys = set()
            for basename, ids in producer_all.items():
                if len(ids) > 1:
                    ambiguous_keys.add(basename)
                    producer_map.pop(basename, None)
                    logger.warning(
                        "Multiple producers for '%s': tasks %s; dependency inference skipped",
                        basename,
                        sorted(ids),
                    )

            # Step 2: Find missing dependencies
            pending_additions: Dict[int, List[int]] = {}  # consumer_id → [producer_ids]
            for node in tree.nodes.values():
                metadata = node.metadata if isinstance(node.metadata, dict) else {}
                contract = self._resolve_task_artifact_contract(node)
                for alias in contract.get("requires", []):
                    if alias in ambiguous_keys:
                        continue
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
                    if basename in ambiguous_keys:
                        continue
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
            expected_edges: List[Tuple[int, int, str]] = []  # (consumer_id, producer_id, basename)
            for consumer_id, additions in pending_additions.items():
                node = tree.nodes[consumer_id]
                new_deps = list(node.dependencies)
                for dep_id in additions:
                    if dep_id not in new_deps:
                        new_deps.append(dep_id)
                normalized = normalize_dependencies_for_task(tree, consumer_id, new_deps)
                for issue in normalized.issues:
                    logger.warning("Dependency edge rejected or normalized: %s", issue.message)
                if normalized.normalized_dependencies == list(node.dependencies):
                    continue
                self._repo.update_task(
                    tree.id,
                    consumer_id,
                    dependencies=normalized.normalized_dependencies,
                )
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

    def _normalize_plan_dependency_edges(self, tree: PlanTree) -> PlanTree:
        """Persist a generic normalized dependency graph before execution.

        This is intentionally structural and task-agnostic: it removes invalid
        ancestor/descendant/self/cyclic dependencies and expands dependencies on
        composite nodes to executable leaf tasks.
        """
        try:
            normalization = normalize_plan_dependencies(tree)
        except Exception as exc:
            logger.warning("Failed to validate plan dependencies: %s", exc)
            return tree

        for issue in normalization.issues:
            level = logging.INFO if issue.code == "composite_dependency_expanded" else logging.WARNING
            logger.log(level, "Plan dependency normalization: %s", issue.message)
            _log_job(
                "info" if level == logging.INFO else "warning",
                "Plan dependency normalization applied.",
                {
                    "plan_id": tree.id,
                    "task_id": issue.task_id,
                    "dependency_id": issue.dependency_id,
                    "code": issue.code,
                    "message": issue.message,
                    "replacement_ids": list(issue.replacement_ids),
                },
            )

        if not normalization.dependencies_by_task:
            return tree

        changed_ids: List[int] = []
        for task_id, deps in normalization.dependencies_by_task.items():
            try:
                self._repo.update_task(tree.id, task_id, dependencies=list(deps))
                changed_ids.append(task_id)
            except Exception as exc:
                logger.warning(
                    "Failed to persist normalized dependencies for plan %s task %s: %s",
                    tree.id,
                    task_id,
                    exc,
                )
        if not changed_ids:
            return tree
        try:
            return self._repo.get_plan_tree(tree.id)
        except Exception as exc:
            logger.warning("Failed to reload plan after dependency normalization: %s", exc)
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
            try:
                loader = get_skills_loader(auto_sync=True)
                cached_generation = config.session_context.get(
                    "plan_skill_candidates_generation"
                )
                if cached_generation is None or cached_generation == getattr(
                    loader, "generation", None
                ):
                    return
                logger.info(
                    "Skills generation changed (%s -> %s); re-selecting plan skill candidates",
                    cached_generation,
                    loader.generation,
                )
            except Exception:
                return

        try:
            loader = get_skills_loader(auto_sync=True)
            available = loader.list_skills()
            if not available:
                logger.info("No skills available; skipping plan-level skill selection")
                config.session_context["plan_skill_candidates"] = []
                config.session_context["plan_skill_candidates_generation"] = getattr(
                    loader, "generation", None
                )
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
            config.session_context["plan_skill_candidates_generation"] = getattr(
                loader, "generation", None
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
          - **Unique basename**: resolved to the matching dependency artifact.
          - **Ambiguous basename**: preserved unresolved and logged. Callers
            must use explicit artifact aliases or exact paths for ambiguous
            artifacts; silently picking the latest producer can mix data
            sources.

        Absolute paths are kept as-is.  Unmatched relative paths are preserved.
        """
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

            dep_ids_with_match = sorted(set(dep_id for dep_id, _ in matches))
            logger.warning(
                "Ambiguous dependency artifact basename '%s' from deps %s; preserving unresolved path.",
                target_basename,
                dep_ids_with_match,
            )
            resolved.append(text)

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

    def _should_delegate_plan_task(self, config: ExecutionConfig) -> bool:
        _ = config
        return str(
            getattr(self._settings, "plan_task_execution_backend", "internal") or "internal"
        ).strip().lower() == "external_agent"

    def _run_task_with_external_delegate(
        self,
        *,
        plan_id: int,
        node: PlanNode,
        parent: Optional[PlanNode],
        dependencies: List[PlanNode],
        plan_outline: Optional[str],
        tree: PlanTree,
        config: ExecutionConfig,
        artifact_contract: Dict[str, List[str]],
        resolved_input_artifacts: Dict[str, str],
        resolved_resources: Dict[str, Dict[str, Any]],
    ) -> ExecutionResult:
        try:
            self._repo.update_task(plan_id, node.id, status="delegating", execution_result="")
            node.status = "delegating"
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Failed to mark task %s as delegating for plan %s: %s",
                node.id,
                plan_id,
                exc,
            )

        session_context = config.session_context if isinstance(config.session_context, dict) else {}
        task_ancestor_chain, task_work_dir = self._resolve_task_tool_workspace(
            node,
            session_id=session_context.get("session_id"),
            tree=tree,
        )
        prompt = self._prompt_builder.build(
            node=node,
            parent=parent,
            dependencies=dependencies,
            plan_outline=plan_outline,
            include_context=config.use_context,
            session_context=session_context,
            include_tool_hints=False,
        )
        node_metadata = node.metadata if isinstance(node.metadata, dict) else {}
        acceptance_criteria = (
            node_metadata.get("acceptance_criteria")
            if isinstance(node_metadata.get("acceptance_criteria"), dict)
            else {}
        )
        readable_dirs = self._delegation_readable_dirs(
            resolved_input_artifacts=resolved_input_artifacts,
            dependencies=dependencies,
            session_context=session_context,
        )
        backend = str(
            getattr(self._settings, "plan_task_agent_backend", "qwen_code") or "qwen_code"
        ).strip().lower()
        delegation = self._task_delegate_executor.execute(
            TaskDelegationSpec(
                plan_id=plan_id,
                task_id=node.id,
                task_name=node.display_name(),
                task_instruction=node.instruction or "",
                task_prompt=prompt,
                executor_backend=backend,
                session_id=session_context.get("session_id"),
                ancestor_chain=task_ancestor_chain,
                owner_id=session_context.get("owner_id"),
                current_job_id=self._current_job_id(),
                work_dir=task_work_dir,
                artifact_contract=dict(artifact_contract or {}),
                acceptance_criteria=dict(acceptance_criteria or {}),
                resolved_input_artifacts=dict(resolved_input_artifacts or {}),
                readable_dirs=readable_dirs,
                resolved_resources=dict(resolved_resources or {}),
            )
        )
        metadata: Dict[str, Any] = {
            "delegated_task_execution": True,
            "executor": delegation.executor,
            "executor_session_id": delegation.executor_session_id,
            "delegation_status": delegation.status,
            **dict(delegation.metadata or {}),
        }
        if delegation.artifact_paths:
            metadata["artifact_paths"] = list(delegation.artifact_paths[:80])
        payload: Dict[str, Any] = {
            "status": "success" if delegation.status == "completed" else "skipped" if delegation.status == "blocked" else "failed",
            "content": delegation.summary,
            "notes": ["Task executed by external code agent delegate."],
            "metadata": metadata,
        }
        if delegation.artifact_paths:
            payload["artifact_paths"] = list(delegation.artifact_paths[:80])
        for key in (
            "run_directory",
            "working_directory",
            "task_directory_full",
            "task_root_directory",
            "results_directory",
            "work_dir",
            "run_dir",
        ):
            value = delegation.raw_result.get(key) if isinstance(delegation.raw_result, dict) else None
            if isinstance(value, str) and value.strip():
                payload[key] = value.strip()
        if delegation.status == "blocked":
            payload.setdefault("metadata", {})["blocked_by_dependencies"] = True
            payload, execution_status = _coerce_blocked_dependency_payload(payload)
            if execution_status == "completed":
                payload["status"] = "skipped"
                execution_status = "skipped"
        elif delegation.status == "failed":
            execution_status = "failed"
        else:
            execution_status = "completed"
        finalization, _ = self._finalize_task_execution(
            plan_id,
            node,
            payload,
            execution_status=execution_status,
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
            content=str(finalization.payload.get("content") or delegation.summary),
            notes=list(finalization.payload.get("notes") or []),
            metadata=finalization.payload.get("metadata") or {},
            raw_response=raw_response,
            attempts=1,
        )

    def _delegation_readable_dirs(
        self,
        *,
        resolved_input_artifacts: Dict[str, str],
        dependencies: List[PlanNode],
        session_context: Dict[str, Any],
    ) -> List[str]:
        dirs: List[str] = []

        def _add(path_value: Any) -> None:
            text = str(path_value or "").strip()
            if not text:
                return
            path = Path(text).expanduser()
            candidate = path if path.exists() and path.is_dir() else path.parent
            candidate_text = str(candidate)
            if candidate_text and candidate_text != "." and candidate_text not in dirs:
                dirs.append(candidate_text)

        for path in resolved_input_artifacts.values():
            _add(path)
        artifact_manifest = self._get_artifact_manifest(
            dependencies[0].plan_id if dependencies else 0,
            session_context,
        ) if dependencies else {}
        for dep in dependencies:
            artifact_context = self._dependency_artifact_context(
                dep,
                session_context.get("_artifact_registry"),
                artifact_manifest=artifact_manifest,
            )
            for path in artifact_context.get("artifact_paths") or []:
                _add(path)
        return dirs[:20]

    @staticmethod
    def _build_output_contract_constraints(node_metadata: Dict[str, Any]) -> List[str]:
        metadata = node_metadata if isinstance(node_metadata, dict) else {}
        acceptance = metadata.get("acceptance_criteria") if isinstance(metadata.get("acceptance_criteria"), dict) else {}
        contract = metadata.get("artifact_contract") if isinstance(metadata.get("artifact_contract"), dict) else {}
        required_paths: List[str] = []
        checks = acceptance.get("checks") if isinstance(acceptance, dict) else None
        if isinstance(checks, list):
            for check in checks:
                if not isinstance(check, dict):
                    continue
                check_type = str(check.get("type") or "").strip().lower()
                if check_type not in {"file_exists", "file_nonempty"}:
                    continue
                path = str(check.get("path") or "").strip()
                if path and path not in required_paths:
                    required_paths.append(path)

        lines: List[str] = []
        if required_paths:
            lines.append(
                "OUTPUT CONTRACT: before claiming completion, create these exact required file(s): "
                + ", ".join(required_paths[:20])
                + ". Similar filenames do not satisfy the contract."
            )
        publishes = contract.get("publishes") if isinstance(contract, dict) else None
        if isinstance(publishes, list) and publishes:
            lines.append(
                "ARTIFACT CONTRACT: this task publishes logical artifact alias(es): "
                + ", ".join(str(item) for item in publishes[:20])
                + ". Ensure the required output files exist so the executor can validate and register them."
            )
        requires = contract.get("requires") if isinstance(contract, dict) else None
        if isinstance(requires, list) and requires:
            lines.append(
                "ARTIFACT INPUTS: use resolved_input_artifacts for required alias(es): "
                + ", ".join(str(item) for item in requires[:20])
                + "; do not resolve ambiguous basenames by guessing."
            )
        return lines

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
        primary_execution_tool_failed = False
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
                    "output_directories": artifact_context.get("output_directories") or [],
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

        output_contract_lines = self._build_output_contract_constraints(node_metadata)
        constraints = [
            "You are in TASK EXECUTION mode, not conversational chat. Complete the task using tools, then produce the output file/result.",
            "Produce actionable output for this task only.",
            "Honor dependency outputs and do not redo completed dependencies.",
            "Do NOT call submit_final_answer — task completion is determined by producing the required output.",
        ]
        constraints.extend(output_contract_lines)
        # Extract tool names mentioned in the instruction and enforce priority
        _instruction_lower = (node.instruction or "").lower()
        _tool_names_in_instruction = [
            t for t in [
                "literature_pipeline", "web_search", "code_executor", "bio_tools",
                "sequence_fetch", "document_reader", "vision_reader", "graph_rag",
                "phagescope_research", "phagescope", "manuscript_writer", "review_pack_writer",
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
                "Do NOT place final deliverables under session workspace/. "
                "If the task instruction or user goal explicitly names an absolute final output directory, "
                "also copy the final deliverables there; the task output directory is a canonical staging/fallback location, not a replacement for a user-requested output directory. "
                "After producing publication-ready files, call deliverable_submit with those final file paths so they appear in the session Deliverables panel."
            )
        if dep_outputs or dependency_paths:
            constraints.append(
                "Before producing this task's final output, inspect the relevant upstream dependency files/directories listed in Dependency Outputs and Paper Context Paths. "
                "Use absolute artifact_paths, dependency output directories, and resolved_input_artifacts as the primary source of truth; if a named non-critical file is absent, continue by examining the dependency output directory for an equivalent JSON/CSV/TSV/MD/TXT artifact instead of stopping early."
            )
        if paper_mode:
            constraints.extend(
                [
                    "Paper mode is enabled: prioritize dependency artifact_paths and paper_context_paths as primary evidence.",
                    "When resolved_input_artifacts or absolute paper_context_paths are provided, treat them as the canonical source of truth instead of guessing filenames.",
                    "Do not claim completion without explicit citation integrity checks against provided reference files.",
                    "CRITICAL TOOL SELECTION: For writing ANY paper content (sections, drafts, revisions, full assembly), you MUST use manuscript_writer. Do NOT use code_executor to write paper text. code_executor may only be used for data analysis, code generation, or non-writing tasks.",
                    "MD-FIRST MANUSCRIPT RULE: write the manuscript source as Markdown (.md) first and make it pass manuscript quality gates before attempting PDF rendering. PDF is a derived deliverable, not the source of truth.",
                    "CRITICAL EVIDENCE GATHERING: When using literature_pipeline to collect evidence for a review or manuscript, set download_pdfs=true (default) and max_pdfs>=20. The review coverage gate requires at least 6 full-text studies; abstract-only cards will block publication.",
                ]
            )
        # --- Compressed file detection and decompression guidance ---
        _compressed_extensions = ('.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tar.xz', '.txz', '.zip', '.rar', '.7z', '.gz')
        _instruction_text = (node.instruction or "").lower()
        _all_paths_to_check = list(dependency_paths or [])
        for dep_out in dep_outputs:
            if isinstance(dep_out.get("artifact_paths"), list):
                _all_paths_to_check.extend(dep_out["artifact_paths"])
        _detected_compressed = []
        for path in _all_paths_to_check:
            path_lower = str(path or "").lower()
            if any(path_lower.endswith(ext) for ext in _compressed_extensions):
                _detected_compressed.append(path)
        _instruction_mentions_compressed = any(ext in _instruction_text for ext in _compressed_extensions)
        if _detected_compressed or _instruction_mentions_compressed:
            _compressed_hint_lines = [
                "COMPRESSED DATA HANDLING: The task involves compressed archive files. "
                "Before reading or analyzing data, you MUST decompress them first using code_executor or terminal_session."
            ]
            if _detected_compressed:
                _compressed_hint_lines.append(
                    f"Detected compressed file(s): {', '.join(_detected_compressed[:5])}. "
                    "Decompress to a working directory (e.g., task_work_dir or session runtime directory) before processing."
                )
            _compressed_hint_lines.extend([
                "Recommended decompression commands:",
                "  - .tar.gz / .tgz: `tar -xzf <file> -C <target_dir>`",
                "  - .tar.bz2 / .tbz2: `tar -xjf <file> -C <target_dir>`",
                "  - .zip: `unzip <file> -d <target_dir>`",
                "  - .gz (single file): `gunzip -k <file>` or `gzip -dk <file>`",
                "After decompression, use file_operations to verify the extracted contents before proceeding with analysis.",
                "Do NOT attempt to read compressed files directly with pandas, csv, or other data libraries — they will fail."
            ])
            constraints.extend(_compressed_hint_lines)
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
            nonlocal primary_execution_tool_failed
            tool_name = str(tool or "").strip().lower()
            if tool_name in _PRIMARY_EXECUTION_TOOLS and payload.get("success") is False:
                primary_execution_tool_failed = True
                tool_result_context["primary_execution_tool_failed"] = True
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

        _job_id_for_stream = self._current_job_id()
        _on_stdout, _on_stderr = self._build_agent_stream_loggers(_job_id_for_stream)

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
                    current_job_id=_job_id_for_stream,
                    work_dir=task_work_dir,
                    channel="plan_executor",
                    mode="task_execution",
                    resolved_resources=(config.session_context or {}).get("resolved_resources") if config.session_context else None,
                    on_stdout=_on_stdout,
                    on_stderr=_on_stderr,
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

            fallback_result = None
            if paper_mode and "manuscript_writer" not in (result.tools_used or []):
                if self._is_leaf_task(node, tree):
                    fallback_result = self._run_manuscript_writer_fallback(
                        node=node,
                        task_context=task_context,
                        session_context=session_context,
                        deep_think_result=result,
                        tool_result_context=tool_result_context,
                        tree=tree,
                    )

            primary_tool_failed = (
                primary_execution_tool_failed
                or _deep_think_has_failed_primary_execution_tool(result)
            )
            deep_think_status = "failed" if primary_tool_failed else "success"
            tools_used = list(result.tools_used or [])
            if fallback_result and fallback_result.get("success"):
                if "manuscript_writer" not in tools_used:
                    tools_used = tools_used + ["manuscript_writer"]
                fallback_paths = self._extract_path_like_values(fallback_result)
                if fallback_paths:
                    existing_paths = [
                        str(item).strip()
                        for item in list(tool_result_context.get("artifact_paths") or [])
                        if str(item).strip()
                    ]
                    tool_result_context["artifact_paths"] = list(
                        dict.fromkeys([*fallback_paths, *existing_paths])
                    )[:40]

            payload: Dict[str, Any] = {
                "status": deep_think_status,
                "content": result.final_answer,
                "notes": [result.thinking_summary] if result.thinking_summary else [],
                "metadata": {
                    "deep_think": True,
                    "confidence": result.confidence,
                    "tools_used": tools_used,
                    "tool_failures": list(getattr(result, "tool_failures", []) or []),
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
            metadata_payload = payload.get("metadata")
            if isinstance(metadata_payload, dict):
                if session_context.get("dependency_warning"):
                    metadata_payload["dependency_warning"] = True
                    metadata_payload["degraded_input"] = True
                incomplete_dependencies = session_context.get("incomplete_dependencies")
                if isinstance(incomplete_dependencies, list) and incomplete_dependencies:
                    metadata_payload["incomplete_dependencies"] = list(incomplete_dependencies)
                missing_artifact_aliases = session_context.get("missing_artifact_aliases")
                if isinstance(missing_artifact_aliases, list) and missing_artifact_aliases:
                    metadata_payload["missing_artifact_aliases"] = list(missing_artifact_aliases)
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
                metadata_payload = payload.get("metadata")
                if isinstance(metadata_payload, dict):
                    metadata_payload["artifact_paths"] = artifact_paths[:40]
            session_artifact_paths = tool_result_context.get("session_artifact_paths")
            if isinstance(session_artifact_paths, list) and session_artifact_paths:
                payload["session_artifact_paths"] = session_artifact_paths[:40]
                metadata_payload = payload.get("metadata")
                if isinstance(metadata_payload, dict):
                    metadata_payload["session_artifact_paths"] = session_artifact_paths[:40]
            payload, effective_execution_status = _coerce_blocked_dependency_payload(payload)
            if (
                primary_tool_failed
                and effective_execution_status == "completed"
                and not _has_recoverable_output_evidence(payload, tool_result_context)
            ):
                effective_execution_status = "failed"
            finalization, _ = self._finalize_task_execution(
                plan_id,
                node,
                payload,
                execution_status=effective_execution_status,
            )
            finalization = self._attempt_contract_repair_with_deep_think(
                plan_id=plan_id,
                node=node,
                task_context=task_context,
                session_context=session_context,
                deep_think_agent=deep_think_agent,
                finalization=finalization,
                tool_result_context=tool_result_context,
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

    @staticmethod
    def _is_leaf_task(node: PlanNode, tree: PlanTree) -> bool:
        return not tree.children_ids(node.id)

    def _derive_manuscript_output_path(self, node: PlanNode) -> str:
        metadata = node.metadata if isinstance(node.metadata, dict) else {}
        acceptance = metadata.get("acceptance_criteria") if isinstance(metadata.get("acceptance_criteria"), dict) else {}
        checks = acceptance.get("checks") if isinstance(acceptance, dict) else None
        if isinstance(checks, list):
            for check in checks:
                if isinstance(check, dict) and check.get("type") == "file_exists":
                    path = str(check.get("path") or "").strip()
                    if path:
                        if path.lower().endswith(".html"):
                            return path[:-5] + ".md"
                        if not path.lower().endswith(".md"):
                            return str(Path(path).with_suffix(".md"))
                        return path
        contract = metadata.get("artifact_contract") if isinstance(metadata, dict) else {}
        publishes = contract.get("publishes") if isinstance(contract, dict) else None
        if isinstance(publishes, list) and publishes:
            for alias in publishes:
                alias_str = str(alias).strip()
                if alias_str:
                    return f"manuscript/{alias_str}.md"
        return f"manuscript/task_{node.id}_manuscript.md"

    def _gather_manuscript_context_paths(
        self,
        node: PlanNode,
        tool_result_context: Dict[str, Any],
        session_context: Dict[str, Any],
    ) -> List[str]:
        paths: List[str] = []
        metadata = node.metadata if isinstance(node.metadata, dict) else {}
        raw_paths = metadata.get("paper_context_paths")
        if isinstance(raw_paths, list):
            for p in raw_paths:
                if isinstance(p, str) and p.strip():
                    paths.append(p.strip())
        artifact_paths = tool_result_context.get("artifact_paths")
        if isinstance(artifact_paths, list):
            for p in artifact_paths:
                if isinstance(p, str) and p.strip() and p.strip() not in paths:
                    paths.append(p.strip())
        session_artifact_paths = tool_result_context.get("session_artifact_paths")
        if isinstance(session_artifact_paths, list):
            for p in session_artifact_paths:
                if isinstance(p, str) and p.strip() and p.strip() not in paths:
                    paths.append(p.strip())
        return paths

    def _derive_manuscript_sections(self, node: PlanNode) -> Optional[List[str]]:
        metadata = node.metadata if isinstance(node.metadata, dict) else {}
        section = metadata.get("paper_section")
        if isinstance(section, str) and section.strip():
            return [section.strip().lower()]
        return None

    def _run_manuscript_writer_fallback(
        self,
        node: PlanNode,
        task_context: TaskExecutionContext,
        session_context: Dict[str, Any],
        deep_think_result: Any,
        tool_result_context: Dict[str, Any],
        tree: Optional[PlanTree] = None,
    ) -> Optional[Dict[str, Any]]:
        try:
            from tool_box.tools_impl.manuscript_writer import manuscript_writer_handler
            from app.services.path_router import PathRouter

            task_desc = (
                f"{node.instruction or ''}\n\n"
                f"Analysis result:\n{getattr(deep_think_result, 'final_answer', '') or ''}"
            ).strip()

            output_path = self._derive_manuscript_output_path(node)
            context_paths = self._gather_manuscript_context_paths(node, tool_result_context, session_context)
            sections = self._derive_manuscript_sections(node)

            # Build ancestor_chain for proper hierarchical directory structure
            ancestor_chain = None
            if tree is not None:
                try:
                    ancestor_chain = PathRouter.build_ancestor_chain(node.id, tree)
                except Exception:
                    ancestor_chain = None

            raw_result = manuscript_writer_handler(
                task=task_desc,
                output_path=output_path,
                context_paths=context_paths,
                sections=sections,
                session_id=session_context.get("session_id"),
                task_id=node.id,
                ancestor_chain=ancestor_chain,
                draft_only=False,
            )
            if asyncio.iscoroutine(raw_result):
                result = self._run_coroutine_sync(raw_result)
            else:
                result = raw_result

            if result and result.get("success"):
                logger.info(
                    "Manuscript writer fallback succeeded for task %s: output=%s",
                    node.id,
                    result.get("output_path"),
                )
            else:
                logger.warning(
                    "Manuscript writer fallback failed for task %s: %s",
                    node.id,
                    result.get("error") if isinstance(result, dict) else "unknown error",
                )

            return result if isinstance(result, dict) else None

        except Exception as exc:
            logger.exception("Manuscript writer fallback error for task %s: %s", node.id, exc)
            return None

    def _attempt_contract_repair_with_deep_think(
        self,
        *,
        plan_id: int,
        node: PlanNode,
        task_context: TaskExecutionContext,
        session_context: Dict[str, Any],
        deep_think_agent: DeepThinkAgent,
        finalization: VerificationFinalization,
        tool_result_context: Dict[str, Any],
    ) -> VerificationFinalization:
        max_attempts = max(0, int(getattr(self._settings, "contract_repair_attempts", 1)))
        if max_attempts <= 0:
            return finalization

        for attempt in range(1, max_attempts + 1):
            metadata = finalization.payload.get("metadata") if isinstance(finalization.payload, dict) else {}
            if not isinstance(metadata, dict):
                return finalization
            if str(metadata.get("failure_kind") or "").strip().lower() != "contract_mismatch":
                return finalization
            contract_diff = metadata.get("contract_diff")
            if not isinstance(contract_diff, dict):
                return finalization

            repair_query = self._build_contract_repair_query(
                node=node,
                attempt=attempt,
                contract_diff=contract_diff,
            )
            repair_context = dict(session_context)
            repair_context["contract_repair"] = {
                "attempt": attempt,
                "max_attempts": max_attempts,
                "contract_diff": contract_diff,
            }
            try:
                _log_job(
                    "warning",
                    "Contract repair attempt started.",
                    {"plan_id": plan_id, "task_id": node.id, "attempt": attempt},
                )
                result = self._run_coroutine_sync(
                    deep_think_agent.think(
                        repair_query,
                        context=repair_context,
                        task_context=task_context,
                    )
                )
            except Exception as exc:
                logger.warning("Contract repair attempt failed for task %s: %s", node.id, exc)
                metadata["contract_repair_error"] = str(exc)
                finalization.payload["metadata"] = metadata
                return finalization

            repair_payload: Dict[str, Any] = {
                "status": "success",
                "content": result.final_answer,
                "notes": [result.thinking_summary] if result.thinking_summary else [],
                "metadata": {
                    "deep_think": True,
                    "contract_repair": True,
                    "repair_attempts": attempt,
                    "confidence": result.confidence,
                    "tools_used": result.tools_used,
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
            artifact_paths = tool_result_context.get("artifact_paths")
            if isinstance(artifact_paths, list) and artifact_paths:
                repair_payload["artifact_paths"] = artifact_paths[:40]
                repair_payload["metadata"]["artifact_paths"] = artifact_paths[:40]

            repair_payload, execution_status = _coerce_blocked_dependency_payload(repair_payload)
            repaired, _ = self._finalize_task_execution(
                plan_id,
                node,
                repair_payload,
                execution_status=execution_status,
                trigger="contract_repair",
            )
            repaired_meta = repaired.payload.get("metadata") if isinstance(repaired.payload, dict) else {}
            if isinstance(repaired_meta, dict):
                repaired_meta["repair_attempts"] = attempt
                repaired.payload["metadata"] = repaired_meta
            finalization = repaired
            _log_job(
                "info" if repaired.final_status == "completed" else "warning",
                "Contract repair attempt finished.",
                {
                    "plan_id": plan_id,
                    "task_id": node.id,
                    "attempt": attempt,
                    "status": repaired.final_status,
                },
            )
            if repaired.final_status == "completed":
                return repaired
        return finalization

    @staticmethod
    def _build_contract_repair_query(
        *,
        node: PlanNode,
        attempt: int,
        contract_diff: Dict[str, Any],
    ) -> str:
        expected = contract_diff.get("expected_deliverables") or []
        missing = contract_diff.get("missing_required_outputs") or []
        wrong_format = contract_diff.get("wrong_format_outputs") or []
        actual = contract_diff.get("actual_outputs") or []
        lines = [
            f"Repair contract mismatch for task #{node.id}: {node.display_name()}.",
            f"Repair attempt: {attempt}.",
            "The previous execution did not satisfy the output contract. Do not redo unrelated work.",
            "First check whether the required artifact already exists in the current task runtime/workspace output directory.",
            "If a valid runtime/workspace artifact exists, report that path as the authoritative output instead of copying it to an external absolute path.",
            "Only create missing outputs inside the current task output directory; do not copy large artifacts to historical backup, upload, or user-environment absolute paths.",
        ]
        if expected:
            lines.append(f"Expected deliverables: {expected}")
        if missing:
            lines.append(f"Missing required outputs: {missing}")
        if wrong_format:
            lines.append(f"Wrong-format outputs: {wrong_format}")
        if actual:
            lines.append(f"Actual outputs currently present: {actual}")
        lines.append("Completion requires passing verification; similar filenames or prose claims are insufficient.")
        return "\n".join(lines)

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
            _job_id_sync = self._current_job_id()
            _on_stdout_sync, _on_stderr_sync = self._build_agent_stream_loggers(_job_id_sync)
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
                    current_job_id=_job_id_sync,
                    work_dir=task_work_dir,
                    channel="plan_executor",
                    mode="task_execution",
                    resolved_resources=(config.session_context or {}).get("resolved_resources") if config.session_context else None,
                    on_stdout=_on_stdout_sync,
                    on_stderr=_on_stderr_sync,
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

    def _build_agent_stream_loggers(self, job_id: str) -> Tuple[Optional[Callable], Optional[Callable]]:
        """Create on_stdout/on_stderr callbacks that parse qwen CLI JSONL and emit structured events."""
        from .decomposition_jobs import plan_decomposition_jobs

        if not job_id:
            return None, None

        async def on_stdout(line: str) -> None:
            line = line.strip()
            if not line:
                return
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                plan_decomposition_jobs.append_log(job_id, "stdout", line, {"sub_type": "raw"})
                return

            if not isinstance(event, dict):
                plan_decomposition_jobs.append_log(job_id, "stdout", line, {"sub_type": "raw"})
                return

            event_type = str(event.get("type") or "").lower()

            if event_type == "assistant":
                message = event.get("message") or {}
                content = message.get("content") or []
                if isinstance(content, list):
                    for part in content:
                        if not isinstance(part, dict):
                            continue
                        part_type = part.get("type")
                        if part_type == "thinking":
                            thought = str(part.get("thinking") or "").strip()
                            if thought:
                                plan_decomposition_jobs.append_log(
                                    job_id, "info", thought,
                                    {"sub_type": "agent_thinking"},
                                )
                        elif part_type == "tool_use":
                            tool_name = str(part.get("name") or "unknown")
                            tool_input = part.get("input") or {}
                            summary = _summarize_tool_input(tool_name, tool_input)
                            plan_decomposition_jobs.append_log(
                                job_id, "info", summary,
                                {"sub_type": "agent_tool_use", "tool_name": tool_name, "tool_input": tool_input},
                            )
                        elif part_type == "text":
                            text = str(part.get("text") or "").strip()
                            if text:
                                plan_decomposition_jobs.append_log(
                                    job_id, "info", text,
                                    {"sub_type": "agent_text"},
                                )
                elif isinstance(content, str) and content.strip():
                    plan_decomposition_jobs.append_log(
                        job_id, "info", content.strip(),
                        {"sub_type": "agent_text"},
                    )

            elif event_type == "user":
                message = event.get("message") or {}
                content = message.get("content") or []
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "tool_result":
                            tool_id = str(part.get("tool_use_id") or "")
                            result_content = part.get("content") or ""
                            if isinstance(result_content, list):
                                result_text = " ".join(
                                    str(p.get("text") or "") for p in result_content if isinstance(p, dict)
                                ).strip()
                            else:
                                result_text = str(result_content).strip()
                            is_error = bool(part.get("is_error"))
                            if result_text:
                                truncated = result_text[:500]
                                plan_decomposition_jobs.append_log(
                                    job_id, "info" if not is_error else "warning", truncated,
                                    {"sub_type": "agent_tool_result", "tool_use_id": tool_id, "is_error": is_error},
                                )

            elif event_type == "result":
                result_text = str(event.get("result") or "").strip()
                usage = event.get("usage") or {}
                if result_text:
                    plan_decomposition_jobs.append_log(
                        job_id, "info", result_text[:500],
                        {"sub_type": "agent_result", "usage": usage},
                    )
            else:
                plan_decomposition_jobs.append_log(job_id, "stdout", line, {"sub_type": "raw"})

        async def on_stderr(line: str) -> None:
            line = line.strip()
            if line:
                plan_decomposition_jobs.append_log(job_id, "stderr", line, {"sub_type": "raw"})

        return on_stdout, on_stderr

    @staticmethod
    def _summarize_tool_input(tool_name: str, tool_input: Any) -> str:
        if not isinstance(tool_input, dict):
            return f"Calling {tool_name}"
        if tool_name == "run_shell_command":
            cmd = str(tool_input.get("command") or "").strip()
            if len(cmd) > 120:
                cmd = cmd[:120] + "..."
            return f"Running: {cmd}" if cmd else f"Calling {tool_name}"
        if tool_name in ("write_file", "edit"):
            path = str(tool_input.get("file_path") or tool_input.get("path") or "").strip()
            name = path.rsplit("/", 1)[-1] if "/" in path else path
            return f"Writing: {name}" if name else f"Calling {tool_name}"
        if tool_name == "read_file":
            path = str(tool_input.get("file_path") or tool_input.get("path") or "").strip()
            name = path.rsplit("/", 1)[-1] if "/" in path else path
            return f"Reading: {name}" if name else f"Calling {tool_name}"
        if tool_name in ("grep_search", "glob"):
            pattern = str(tool_input.get("pattern") or tool_input.get("query") or "").strip()
            return f"Searching: {pattern}" if pattern else f"Calling {tool_name}"
        if tool_name == "todo_write":
            todos = tool_input.get("todos") or []
            if isinstance(todos, list):
                active = [t for t in todos if isinstance(t, dict) and t.get("status") == "in_progress"]
                if active:
                    return f"Working on: {active[0].get('content', 'task')}"
            return "Updating todo list"
        return f"Calling {tool_name}"

    @staticmethod
    def _normalize_status(raw: str) -> str:
        # Single source of truth: TaskVerificationService._normalize_status (D4 convergence).
        return TaskVerificationService._normalize_status(raw)


__all__ = [
    "ExecutionConfig",
    "ExecutionResponse",
    "ExecutionResult",
    "ExecutionSummary",
    "ExecutorPromptBuilder",
    "PlanExecutor",
    "PlanExecutorLLMService",
]
