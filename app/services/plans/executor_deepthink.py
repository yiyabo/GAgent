"""DeepThink delegation + qwen stream-translation cluster of ``PlanExecutor``.

Moved verbatim out of ``plan_executor.py`` per
``design/2026-09-24-backend-godfiles-refactor-plan.md`` §4.6 (cluster ⑤):
the biggest single method (``_run_task_with_deep_think``, 604 lines), the
manuscript-writer fallback trio, the contract-repair pair, and the qwen CLI
JSONL→job-event translation layer (``_build_agent_stream_loggers`` +
``_summarize_tool_input`` + ``_current_job_id``).  Composed into
``PlanExecutor`` as the ``_DeepThinkMethods`` mixin so every ``self.*`` call
site is unchanged.

Monkeypatch surface (the reason this module has a late-bound helper):

- ``DeepThinkAgent`` is patched **on the facade module** — 7 direct
  ``plan_executor_module.DeepThinkAgent = ...`` assignments plus 2
  ``monkeypatch.setattr("app.services.plans.plan_executor.DeepThinkAgent", ...)``
  (``app/tests/plan/test_plan_executor_deps.py``,
  ``app/tests/plan/test_dependency_planner.py``).  The single construction site
  therefore reads ``_facade().DeepThinkAgent(...)`` at call time (the
  ``_dta().DeepThinkAgent`` precedent of the deep_think split).  This is the
  one body deviation from byte-verbatim; the class is also imported under
  ``TYPE_CHECKING`` for the ``deep_think_agent: DeepThinkAgent`` annotation.
- ``_run_coroutine_sync`` is the module function of ``executor_text_utils``
  (re-exported by the facade).  The former same-named 2-line delegate method
  was deleted by the D13 convergence commit and every call site now calls the
  function directly; the name is unpatched anywhere (0 test references).
- ``__file__`` is used once (session runtime directory pre-computation): the
  sibling lives in the same ``app/services/plans/`` directory, so the
  ``dirname**4`` project-root arithmetic resolves to the same path.
- Function-local imports (``tool_box...manuscript_writer``, ``app.services
  .path_router``, ``.decomposition_jobs``, ``app.services.artifacts``) are
  preserved verbatim.

The module uses its own ``logging.getLogger(__name__)`` (split precedent).
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
import json
import logging
import os
from app.services.run_budget import RunDeadlineExceeded, check_run_active
from app.repository.run_steps import StaleRunClaim
from app.services.deep_think.checkpointing import ControllerRestoreError
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from ..deep_think_agent import (
    TaskExecutionContext,
    ThinkingStep,
    build_user_visible_step,
    detect_reasoning_language,
)
from ..execution.tool_executor import ToolExecutionContext, UnifiedToolExecutor
from ..skills import get_skills_loader
from .executor_models import ExecutionConfig, ExecutionResult
from .executor_text_utils import (
    _PRIMARY_EXECUTION_TOOLS,
    _coerce_blocked_dependency_payload,
    _deep_think_has_failed_primary_execution_tool,
    _has_recoverable_output_evidence,
    _log_job,
    _run_coroutine_sync,
)
from .plan_models import PlanNode, PlanTree
from .paper_task_policy import is_planning_document, requires_manuscript_content, PLANNING_DOCUMENT_SCOPE
from .executor_resume_repair import has_existing_evidence, previous_delivery_gap
from .executor_history import history_scope, load_history, save_history
from .task_verification import VerificationFinalization

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..deep_think_agent import DeepThinkAgent

logger = logging.getLogger(__name__)


def _facade() -> Any:
    """Late-bound plan_executor facade module (monkeypatch-friendly lookups)."""
    from . import plan_executor

    return plan_executor


def _node_declares_outputs(node: PlanNode) -> bool:
    """True when the node's own metadata declares outputs or publish aliases."""
    metadata = node.metadata if isinstance(node.metadata, dict) else {}
    from .output_spec import output_spec_from_metadata

    spec = output_spec_from_metadata(metadata)
    if spec is not None and getattr(spec, "required_outputs", None):
        return True
    raw_required = metadata.get("required_outputs")
    if isinstance(raw_required, list) and raw_required:
        return True
    spec_raw = metadata.get("output_spec") if isinstance(metadata.get("output_spec"), dict) else {}
    contract = metadata.get("artifact_contract") or spec_raw.get("artifact_contract") or {}
    if isinstance(contract, dict) and contract.get("publishes"):
        return True
    return False


def _task_ran_without_output(
    *,
    node: PlanNode,
    result: Any,
    tools_used: List[str],
    payload: Dict[str, Any],
    tool_result_context: Dict[str, Any],
) -> bool:
    """True when a deep-think run used no primary execution tool and left no output.

    A "success" in that state really means "the model answered in prose": the
    task neither executed anything nor produced artifacts, so the honest
    status is needs_review — a human decides whether the answer suffices.
    Nodes that declare their own outputs are excluded: for those, the artifact
    contract / verification path already decides pass or fail.
    """
    if any(tool in _PRIMARY_EXECUTION_TOOLS for tool in (tools_used or [])):
        return False
    if _node_declares_outputs(node):
        return False
    if _has_recoverable_output_evidence(payload, tool_result_context):
        return False
    verification = getattr(result, "output_verification", None)
    if isinstance(verification, dict) and verification.get("artifact_paths"):
        return False
    return True


class _DeepThinkMethods:
    """DeepThink delegation + qwen stream translation (mixin)."""

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
        repair_finalization: Optional[VerificationFinalization] = None,
    ) -> ExecutionResult:
        check_run_active()
        self._contract_repair_limit(config)
        prior_delivery_gap = (
            previous_delivery_gap(node)
            if (repair_finalization is None and self._contract_repair_limit(config)
                and not config.force_rerun) else None
        )
        if (config.session_context or {}).get('input_binding_changed'):
            prior_delivery_gap = None
            repair_finalization = None
        try:
            retained_failure = repair_finalization or prior_delivery_gap
            self._repo.update_task(plan_id, node.id, status="running", execution_result=(
                json.dumps(retained_failure.payload, ensure_ascii=False) if retained_failure else ""))
            node.status = "running"
        except (RunDeadlineExceeded, StaleRunClaim, ControllerRestoreError):
            raise
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Failed to mark task %s as running for plan %s: %s",
                node.id,
                plan_id,
                exc,
            )

        session_context = dict(config.session_context or {})
        node_metadata = node.metadata if isinstance(node.metadata, dict) else {}
        for key in ("output_spec", "output_input_snapshot", "output_spec_base_dir"):
            if key in node_metadata:
                session_context[key] = node_metadata[key]
        planning_document = is_planning_document(node.name, node.instruction, node_metadata)
        paper_mode = not planning_document and bool(
            config.paper_mode
            or session_context.get("paper_mode")
            or node_metadata.get("paper_mode")
        )
        if planning_document:
            # Existing persisted trees may contain the old section_writer tag.
            # Correct this run's instructions without editing that task tree.
            session_context["paper_mode"] = False
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
        if (repair_finalization is None and prior_delivery_gap is not None
                and (prior_delivery_gap.payload.get("metadata", {}).get("execution_history_ref")
                     or has_existing_evidence(prior_delivery_gap, dependency_paths, task_work_dir))):
            repair_finalization = prior_delivery_gap
            _log_job("info", "复用已有证据，优先补齐上次验收的缺项。", {
                "sub_type": "repair_resume", "plan_id": plan_id, "task_id": node.id,
            })

        if paper_mode:
            session_context["paper_mode"] = True
            if dependency_paths:
                session_context["paper_context_paths"] = dependency_paths[:40]

        output_contract_lines = self._build_output_contract_constraints(node_metadata)
        constraints = [
            "You are in TASK EXECUTION mode, not conversational chat. Complete the task using tools, then produce the output file/result.",
            "Produce actionable output for this task only.",
            "Honor dependency outputs and do not redo completed dependencies.",
            "After producing and checking the required outputs, call submit_final_answer to deliver their paths and your result. "
            "For the strict JSON protocol, use its final_answer field instead. "
            "This submits your answer; PlanExecutor independently checks OutputSpec and determines task completion.",
        ]
        constraints.extend(output_contract_lines)
        from .completion_policy import acceptance_enabled, guidance
        if not acceptance_enabled():
            constraints = [line for line in constraints if "independently checks OutputSpec" not in line]
            constraints.append("PlanExecutor records this task's execution lifecycle. When finished, use submit_final_answer in native mode or the final_answer field in strict mode to deliver your result and file paths.")
            constraints.append(guidance())
        from . import input_discovery
        input_prompt = input_discovery.prompt(session_context)
        if input_prompt:
            constraints.append(input_prompt)
        if planning_document:
            constraints.append(PLANNING_DOCUMENT_SCOPE)
        # Extract tool names mentioned in the instruction and enforce priority
        _instruction_lower = (node.instruction or "").lower()
        _tool_names_in_instruction = [
            t for t in [
                "literature_pipeline", "web_search", "code_executor", "bio_tools",
                "sequence_fetch", "ncbi_search", "document_reader", "vision_reader", "graph_rag",
                "phagescope_research", "phagescope", "manuscript_writer", "review_pack_writer",
                "file_operations", "terminal_session", "deliverable_submit",
            ]
            if t in _instruction_lower
        ]
        if _tool_names_in_instruction:
            constraints.append(
                f"CRITICAL: The task instruction explicitly specifies tool(s): {', '.join(_tool_names_in_instruction)}. "
                "Honor the tools explicitly required as methods, rather than treating an incidental or negated mention as a command. "
                "Supporting inspection, computation and file-write tools remain available to complete the same output contract."
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
                    "MD-FIRST MANUSCRIPT RULE: write the manuscript source as Markdown (.md) first and make it pass manuscript quality gates before attempting PDF rendering. PDF is a derived deliverable, not the source of truth.",
                    "CRITICAL EVIDENCE GATHERING: When using literature_pipeline to collect evidence for a review or manuscript, set download_pdfs=true (default) and max_pdfs>=20. The review coverage gate requires at least 6 full-text studies; abstract-only cards will block publication.",
                ]
            )
            if requires_manuscript_content(node.name, node.instruction, node_metadata):
                constraints.append(
                    "For substantive manuscript drafting, use manuscript_writer with the supplied evidence. "
                    "Task completion still depends on the actual output and content checks, not the tool name."
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
                    "Inspect the format first. Extract only required archive members using available execute_code, code_executor or terminal_session tools. "
                    "For a single compressed data file, use a reader that supports its compression when possible."
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
                "Do not pass archive containers directly to a table reader. Libraries such as pandas can read supported single-file compression; use extraction when the reader cannot handle the format."
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
        if config.enable_skills and repair_finalization is None:
            try:
                loader = get_skills_loader(auto_sync=False)
                tool_hints = self._derive_skill_tool_hints(
                    task_text=user_query,
                    dependency_paths=dependency_paths,
                    paper_mode=paper_mode,
                )
                selection_result = _run_coroutine_sync(
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
            except (RunDeadlineExceeded, StaleRunClaim, ControllerRestoreError):
                raise
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
            output_spec=node_metadata.get("output_spec"),
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
            input_discovery.restore_receipt(session_context, node,
                self._get_artifact_manifest(plan_id, session_context), payload)
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
            blocked = input_discovery.guard(session_context, tool_name, params)
            if blocked:
                return blocked
            if tool_name == 'plan_operation' and params.get('operation') == 'resolve_input':
                return input_discovery.bind(session_context, node,
                    self._get_artifact_manifest(plan_id, session_context), params)
            response = await self._tool_executor.execute(
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
            input_discovery.note_read(session_context, tool_name, params, response)
            return response

        from .execution_budget import request_profile
        repair_resume = bool(repair_finalization and
                             repair_finalization.payload.get("metadata", {}).get("resumed_delivery_gap"))
        tool_names = [
            "web_search", "graph_rag", "sequence_fetch", "ncbi_search", "code_executor",
            "file_operations", "document_reader", "vision_reader", "bio_tools",
            "literature_pipeline", "review_pack_writer", "phagescope_research",
            "phagescope", "plan_operation", "manuscript_writer",
            "terminal_session", "deliverable_submit",
        ]
        if repair_resume:
            tool_names = [name for name in tool_names if name not in {
                "web_search", "graph_rag", "sequence_fetch", "ncbi_search", "literature_pipeline",
                "phagescope_research", "phagescope", "plan_operation",
            }]
            tool_names.append("scientific_figure_generator")
        from app.services.foundation.settings import get_settings
        from app.services.deep_think.text_utils import _DEEP_THINK_THINKING_BUDGET
        session_context["execution_history_scope"] = history_scope(node, session_context, dep_outputs)
        deep_think_agent = _facade().DeepThinkAgent(
            llm_client=self._llm._llm,
            available_tools=_execution_tools(tool_names),
            tool_executor=_tool_wrapper,
            max_iterations=getattr(self._settings, "deep_think_max_iterations", 16),
            tool_timeout=UnifiedToolExecutor.DEFAULT_TIMEOUT_SECONDS,
            on_thinking=on_thinking,
            on_thinking_delta=on_thinking_delta,
            on_tool_start=on_tool_start,
            on_tool_result=on_tool_result,
            request_profile=request_profile(node.plan_id, node.id, session_context),
            enable_thinking=get_settings().thinking_enabled,
            thinking_budget=_DEEP_THINK_THINKING_BUDGET,
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
            if repair_finalization is not None:
                history_ref = repair_finalization.payload.get("metadata", {}).get("execution_history_ref")
                try:
                    continuation = load_history(history_ref, task_work_dir, session_context["execution_history_scope"])
                except ControllerRestoreError as exc:
                    check_run_active()
                    failed_payload = deepcopy(repair_finalization.payload)
                    failed_payload.setdefault("metadata", {})["execution_issues"] = [
                        {"code": "step_reconciliation_required", "reason": str(exc)}]
                    self._repo.update_task(plan_id, node.id, status="failed",
                        execution_result=json.dumps(failed_payload, ensure_ascii=False))
                    raise
                if history_ref and continuation is None:
                    repair_finalization = None  # Inputs changed: execute the current task afresh.
                elif continuation:
                    session_context["execution_continuation"] = continuation
            if repair_finalization is not None:
                finalization = self._attempt_contract_repair_with_deep_think(
                    plan_id=plan_id, node=node, task_context=task_context,
                    session_context=session_context, deep_think_agent=deep_think_agent,
                    finalization=repair_finalization, tool_result_context=tool_result_context, config=config)
                finalization, raw_response = self._materialize_finalization(
                    plan_id, node, finalization, session_context=config.session_context)
                node.execution_result, node.status = raw_response, finalization.final_status
                tree.nodes[node.id] = node
                return ExecutionResult(plan_id=plan_id, task_id=node.id, status=finalization.final_status,
                    content=str(finalization.payload.get('content') or ''),
                    metadata=finalization.payload.get('metadata') or {}, raw_response=raw_response, attempts=1)
            result = _run_coroutine_sync(
                deep_think_agent.think(
                    user_query,
                    context=session_context,
                    task_context=task_context,
                )
            )
            check_run_active()
            history_ref = save_history(task_work_dir, getattr(result, "continuation", None))

            primary_tool_failed = (
                primary_execution_tool_failed
                or _deep_think_has_failed_primary_execution_tool(result)
            )
            deep_think_status = "failed" if primary_tool_failed else "success"
            if getattr(result, 'termination_reason', None) == 'llm_unavailable':
                deep_think_status = 'failed'
            execution_issues = list(getattr(result, "execution_issues", None) or [])
            if execution_issues:
                deep_think_status = "failed"
            tools_used = list(result.tools_used or [])
            payload: Dict[str, Any] = {
                "status": deep_think_status,
                "content": result.final_answer,
                "notes": [result.thinking_summary] if result.thinking_summary else [],
                "metadata": {
                    "deep_think": True,
                    "termination_reason": getattr(result, "termination_reason", None),
                    "confidence": result.confidence,
                    "tools_used": tools_used,
                    "tool_failures": list(getattr(result, "tool_failures", []) or []),
                    "execution_issues": execution_issues,
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
                if history_ref:
                    metadata_payload["execution_history_ref"] = history_ref
                for key in ("output_spec", "output_input_snapshot", "output_spec_base_dir", "output_verification"):
                    value = getattr(result, key, None)
                    if value is not None:
                        metadata_payload[key] = value
                output_verification = getattr(result, "output_verification", None)
                if isinstance(output_verification, dict):
                    metadata_payload["output_produced_paths"] = list(output_verification.get("artifact_paths") or [])
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
            from .completion_policy import acceptance_enabled
            if acceptance_enabled() and deep_think_status == "success" and _task_ran_without_output(
                node=node,
                result=result,
                tools_used=tools_used,
                payload=payload,
                tool_result_context=tool_result_context,
            ):
                # Nothing executed and nothing produced: report needs_review
                # instead of a bare "success" (2026-10-11).
                deep_think_status = "needs_review"
                payload["status"] = deep_think_status
            payload, effective_execution_status = _coerce_blocked_dependency_payload(payload)
            if str(payload.get("status") or "").strip().lower() == "needs_review":
                effective_execution_status = "needs_review"
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
            # Verify before choosing recovery; publication/result persistence
            # happens only after these checks and any targeted repair.
            # An accepted deliverable finishes regardless of which writer was
            # used. Retain the legacy writer recovery only for a real, missing
            # manuscript output, never as a completion prerequisite.
            if (paper_mode and self._is_leaf_task(node, tree)
                    and self._needs_manuscript_recovery(node, finalization, task_work_dir)
                    and "manuscript_writer" not in tools_used):
                _log_job("info", "验收仍有缺项，尝试补齐论文交付。", {
                    "sub_type": "repair_progress", "task_id": node.id,
                    "status": "repairing", "method": "manuscript_writer",
                })
                fallback_result = self._run_manuscript_writer_fallback(
                    node=node, task_context=task_context, session_context=session_context,
                    deep_think_result=result, tool_result_context=tool_result_context, tree=tree)
                if fallback_result and fallback_result.get("success"):
                    paths = list(dict.fromkeys([*payload.get("artifact_paths", []),
                        *self._extract_path_like_values(fallback_result)]))[:40]
                    tool_result_context["artifact_paths"] = paths
                    payload["artifact_paths"] = paths
                    payload["metadata"].update(artifact_paths=paths,
                        tools_used=[*tools_used, "manuscript_writer"])
                    payload["metadata"].pop("output_produced_paths", None)
                    finalization, _ = self._finalize_task_execution(
                        plan_id, node, payload, execution_status=effective_execution_status)
            finalization = self._attempt_contract_repair_with_deep_think(
                plan_id=plan_id,
                node=node,
                task_context=task_context,
                session_context=session_context,
                deep_think_agent=deep_think_agent,
                finalization=finalization,
                tool_result_context=tool_result_context,
                config=config,
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
        except (RunDeadlineExceeded, StaleRunClaim, ControllerRestoreError):
            raise
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

    @staticmethod
    def _is_leaf_task(node: PlanNode, tree: PlanTree) -> bool:
        return not tree.children_ids(node.id)

    def _derive_manuscript_output_path(self, node: PlanNode) -> str:
        metadata = node.metadata if isinstance(node.metadata, dict) else {}
        from .output_spec import output_spec_from_metadata, spec_metadata_view
        spec = output_spec_from_metadata(metadata)
        metadata = spec_metadata_view(metadata, spec)
        for output in (spec.required_outputs if spec else []):
            path = str(output.target_path or "").strip()
            if output.kind == "document" and output.min_count == 1 and Path(path).suffix.lower() in {".md", ".markdown", ".html"}:
                return str(Path(path).with_suffix(".md")) if path.lower().endswith(".html") else path
        acceptance = metadata.get("acceptance_criteria") if isinstance(metadata.get("acceptance_criteria"), dict) else {}
        checks = acceptance.get("checks") if isinstance(acceptance, dict) else None
        if isinstance(checks, list):
            for check in checks:
                if isinstance(check, dict) and check.get("type") in {"file_exists", "file_nonempty"}:
                    path = str(check.get("path") or "").strip()
                    if Path(path).suffix.lower() in {".md", ".markdown", ".html"}:
                        if path.lower().endswith(".html"):
                            return path[:-5] + ".md"
                        return path
        contract = metadata.get("artifact_contract") if isinstance(metadata, dict) else {}
        publishes = contract.get("publishes") if isinstance(contract, dict) else None
        if isinstance(publishes, list) and publishes:
            from .artifact_contracts import candidate_filenames_for_alias
            for alias in publishes:
                for name in candidate_filenames_for_alias(str(alias)):
                    if Path(name).suffix.lower() in {".md", ".markdown"}:
                        return name
        return f"manuscript/task_{node.id}_manuscript.md"

    def _needs_manuscript_recovery(
        self, node: PlanNode, finalization: VerificationFinalization, task_work_dir: str
    ) -> bool:
        if (not self._should_repair_contract(finalization)
                or not requires_manuscript_content(node.name, node.instruction, node.metadata)):
            return False
        from .output_spec import output_spec_from_metadata, spec_metadata_view
        spec = output_spec_from_metadata(node.metadata)
        if spec and spec.required_outputs and not any(
            output.kind == "document" and output.min_count == 1 and output.target_path
            and Path(output.target_path).suffix.lower() in {".md", ".markdown", ".html"}
            for output in spec.required_outputs
        ):
            return False  # the existing repair loop handles other/multiple outputs
        if not (spec and spec.required_outputs):
            metadata = node.metadata if isinstance(node.metadata, dict) else {}
            metadata = spec_metadata_view(metadata, spec)
            checks = (metadata.get("acceptance_criteria") or {}).get("checks") or []
            from .artifact_contracts import candidate_filenames_for_alias
            aliases = (metadata.get("artifact_contract") or {}).get("publishes") or []
            declared_paths = [str(check.get("path") or "") for check in checks
                if isinstance(check, dict) and check.get("type") in {"file_exists", "file_nonempty"}]
            declared_paths.extend(name for alias in aliases for name in candidate_filenames_for_alias(str(alias)))
            if not any(Path(path).suffix.lower() in {".md", ".markdown", ".html"} for path in declared_paths):
                return False  # do not invent an output target for a heavy writer
        path = Path(self._derive_manuscript_output_path(node))
        if not path.is_absolute():
            path = Path(task_work_dir) / path
        # Content/quality failure or a missing *other* output is not permission
        # to overwrite an existing manuscript. Targeted repair owns those gaps.
        try:
            return not (path.is_file() and path.stat().st_size > 0)
        except OSError:
            return False  # uncertain file state belongs to targeted repair

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
                result = _run_coroutine_sync(raw_result)
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

        except (RunDeadlineExceeded, StaleRunClaim, ControllerRestoreError):
            raise
        except Exception as exc:
            logger.exception("Manuscript writer fallback error for task %s: %s", node.id, exc)
            return None

    def _contract_repair_limit(self, config: Optional[ExecutionConfig] = None) -> int:
        from .completion_policy import acceptance_enabled
        if not acceptance_enabled():
            return 0
        value = getattr(config, "contract_repair_attempts", None)
        if value is None:
            value = getattr(self._settings, "contract_repair_attempts", 2)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 3:
            raise ValueError("contract_repair_attempts must be an integer from 0 to 3")
        return value

    @staticmethod
    def _should_repair_contract(finalization: VerificationFinalization) -> bool:
        from .completion_policy import acceptance_enabled
        if not acceptance_enabled():
            return False
        metadata = finalization.payload.get("metadata") or {}
        return (finalization.final_status == "failed"
                and metadata.get("failure_kind") == "contract_mismatch"
                and isinstance(metadata.get("contract_diff"), dict)
                and not metadata.get("blocked_by_dependencies")
                and metadata.get("termination_reason") not in {"cancelled", "run_deadline_exceeded", "llm_unavailable"})

    def _attempt_contract_repair_with_deep_think(
        self, *, plan_id: int, node: PlanNode, task_context: TaskExecutionContext,
        session_context: Dict[str, Any], deep_think_agent: DeepThinkAgent,
        finalization: VerificationFinalization, tool_result_context: Dict[str, Any],
        config: Optional[ExecutionConfig] = None,
    ) -> VerificationFinalization:
        """Feed the failed checks back to the existing agent, then verify again."""
        from app.llm import usage_context_override

        max_attempts = self._contract_repair_limit(config)
        if not max_attempts or not self._should_repair_contract(finalization):
            return finalization
        original_limit = getattr(deep_think_agent, "max_iterations", None)
        history = []
        continuation = session_context.get("execution_continuation")
        if continuation is None:
            metadata = finalization.payload.get("metadata") or {}
            continuation = load_history(metadata.get("execution_history_ref"),
                metadata.get("output_spec_base_dir"), session_context.get("execution_history_scope"))
        try:
            for attempt in range(1, max_attempts + 1):
                check_run_active()
                if not self._should_repair_contract(finalization):
                    break
                metadata = finalization.payload.get("metadata") or {}
                diff = metadata["contract_diff"]
                failures = (metadata.get("output_verification") or metadata.get("verification") or {}).get("failures") or []
                missing_count = sum(
                    max(0, item["required_count"] - item["actual_count"])
                    for item in failures if isinstance(item, dict)
                    and isinstance(item.get("required_count"), int)
                    and isinstance(item.get("actual_count"), int)
                )
                # A missing chapter set needs room for several writes. Keep the
                # existing 12-step cap for one-file repairs and the same two
                # attempts/deadline for both paths.
                if original_limit is not None:
                    deep_think_agent.max_iterations = min(original_limit, 24 if missing_count >= 2 else 12)
                repair_query = self._build_contract_repair_query(
                    node=node, attempt=attempt, contract_diff=diff, failed_checks=failures,
                    base_dir=metadata.get("output_spec_base_dir"))
                recent_errors = metadata.get("tool_failures") or metadata.get("execution_issues") or []
                errors = [str(item.get("error") or item.get("message") or item.get("summary") or "")
                          if isinstance(item, dict) else str(item) for item in recent_errors]
                repair_query += "\nRecent errors:\n" + "\n".join(errors)[-4000:]
                repair_query += "\nFailed checks:\n" + "\n".join(
                    f"- {item.get('path') or item.get('type')}: {item.get('message') or 'check failed'}"
                    for item in failures if isinstance(item, dict))[:4000]
                repair_query += "\nOriginal content requirements (do not rerun the original workflow): " + str(node.instruction or node.name)
                repair_query += "\nRead the actual JSON/table fields before using them. Preserve outputs that already passed. Use existing local evidence and keep its citations/statistics consistent; do not invent missing facts or repeat the research."
                if attempt > 1:
                    repair_query += "\nThe previous method did not work. Use a simpler method for the missing output; for Markdown/text, write short sections directly instead of continuing to patch a failing renderer."
                repair_context = {**session_context, "paper_mode": False,
                                  "contract_repair": {"attempt": attempt, "max_attempts": max_attempts, "contract_diff": diff}}
                if continuation:
                    repair_context["execution_continuation"] = continuation
                else:
                    # Legacy runs have no full transcript. Their candidate answer
                    # remains evidence, not proof that a required file was saved.
                    previous_answer = str(finalization.payload.get("content") or "")
                    repair_query += "\nPrevious candidate response (not a delivered file):\n" + previous_answer
                base_dir = str(metadata.get("output_spec_base_dir") or "").strip()
                repair_constraints = [
                    "Reuse existing evidence; do not restart the original workflow or alter passed files.",
                    "Honor the original output/content requirements and verify actual input fields before writing.",
                    "Use submit_final_answer (native) or final_answer (strict) when done. PlanExecutor verifies and publishes the files.",
                ]
                if base_dir:
                    repair_constraints.insert(0, f"Canonical task output directory: {base_dir}. Resolve relative required outputs beneath this directory.")
                repair_task = replace(task_context, plan_outline=None, skill_context=None,
                    task_instruction="Complete only the missing deliverables from the previous execution.",
                    constraints=repair_constraints)
                event = {"sub_type": "repair_progress", "plan_id": plan_id, "task_id": node.id,
                         "attempt": attempt, "max_attempts": max_attempts, "status": "repairing"}
                _log_job("warning", f"正在补齐缺失产物（{attempt}/{max_attempts}）", event)
                try:
                    with usage_context_override(phase="plan_repair", call_purpose="plan_contract_repair"):
                        result = _run_coroutine_sync(deep_think_agent.think(
                            repair_query, context=repair_context, task_context=repair_task))
                    check_run_active()
                except (RunDeadlineExceeded, StaleRunClaim, ControllerRestoreError):
                    raise
                except Exception as exc:
                    metadata["contract_repair_error"] = str(exc)
                    metadata["repair_attempts"] = attempt
                    _log_job("warning", "补交中断，保留已有结果。", {**event, "status": "failed", "error": str(exc)})
                    break
                payload = deepcopy(finalization.payload)
                payload.update(status="success", content=result.final_answer)
                repaired_metadata = payload.setdefault("metadata", {})
                repaired_metadata.update(contract_repair=True, repair_attempts=attempt,
                    termination_reason=getattr(result, "termination_reason", None),
                    tools_used=list(result.tools_used or []),
                    tool_failures=list(getattr(result, "tool_failures", []) or []),
                    execution_issues=list(getattr(result, "execution_issues", []) or []))
                continuation = getattr(result, "continuation", None)
                history_ref = save_history(base_dir, continuation)
                if history_ref:
                    repaired_metadata["execution_history_ref"] = history_ref
                paths = list(dict.fromkeys([*payload.get("artifact_paths", []),
                    *tool_result_context.get("artifact_paths", []),
                    *self._extract_path_like_values(result.final_answer)]))
                payload["artifact_paths"] = paths
                repaired_metadata["artifact_paths"] = paths
                # Do not let the first attempt's empty produced-path list hide
                # files which the repair controller actually wrote and checked.
                checked = getattr(result, "output_verification", None)
                previous = repaired_metadata.pop("output_produced_paths", None) or []
                if previous or (isinstance(checked, dict) and checked.get("artifact_paths")):
                    repaired_metadata["output_produced_paths"] = list(dict.fromkeys([
                        *previous, *(checked.get("artifact_paths", []) if isinstance(checked, dict) else [])]))
                payload, execution_status = _coerce_blocked_dependency_payload(payload)
                finalization, _ = self._finalize_task_execution(
                    plan_id, node, payload, execution_status=execution_status, trigger="contract_repair")
                history.append({"attempt": attempt, "status": finalization.final_status,
                    "remaining": finalization.payload.get("metadata", {}).get("contract_diff")})
                finalization.payload.setdefault("metadata", {})["repair_history"] = list(history)
                _log_job("info" if finalization.final_status == "completed" else "warning",
                    "补交验收通过。" if finalization.final_status == "completed" else "补交仍未通过验收。",
                    {**event, "status": finalization.final_status})
        finally:
            if original_limit is not None:
                deep_think_agent.max_iterations = original_limit
        return finalization

    @staticmethod
    def _build_contract_repair_query(
        *,
        node: PlanNode,
        attempt: int,
        contract_diff: Dict[str, Any],
        failed_checks: Optional[List[Dict[str, Any]]] = None,
        base_dir: Optional[str] = None,
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
            "Reuse a valid existing artifact, but satisfy the exact target path in the current output contract. Resolve relative targets against the canonical task output directory; preserve an explicitly required absolute target when the runtime permits it.",
            "Do not copy artifacts to unrelated historical backup or upload paths. If the exact requested target cannot be written, report that blocker instead of silently substituting another path.",
        ]
        if expected:
            lines.append(f"Expected deliverables: {expected}")
        if missing:
            lines.append(f"Missing required outputs: {missing}")
        if wrong_format:
            lines.append(f"Wrong-format outputs: {wrong_format}")
        for check in failed_checks or []:
            if not isinstance(check, dict):
                continue
            required, actual_count = check.get("required_count"), check.get("actual_count")
            path = str(check.get("path") or "").strip()
            if path and isinstance(required, int) and isinstance(actual_count, int) and required > actual_count:
                extensions = check.get("extensions") or []
                suffixes = ", ".join(str(ext) for ext in extensions) if isinstance(extensions, list) else ""
                target = path
                if base_dir and Path(base_dir).is_absolute() and not Path(path).is_absolute() and ".." not in Path(path).parts:
                    target = str(Path(base_dir) / path)
                lines.append(
                    f"AUTHORITATIVE FILE DEFICIT: {path} requires {required} files"
                    f"{f' ({suffixes})' if suffixes else ''}; the verifier found {actual_count}. "
                    f"Create at least {required - actual_count} additional files directly in {target} before finishing."
                )
        if actual:
            if isinstance(actual, list) and len(actual) > 12:
                lines.append(
                    f"The previous run reported {len(actual)} output paths; do not audit unrelated files. "
                    "Inspect the failed target directory and reuse the existing evidence."
                )
            else:
                lines.append(f"Actual outputs currently present: {actual}")
        lines.append(
            "Write the missing deliverable files now. A diagnosis, plan or final-answer claim "
            "without those files does not satisfy this task."
        )
        lines.append("Completion requires passing verification; similar filenames or prose claims are insufficient.")
        return "\n".join(lines)

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


def _execution_tools(names):
    from app.services.tool_schemas import code_mode_enabled
    names=list(names)+['load_skill']
    if code_mode_enabled():names.append('execute_code')
    return list(dict.fromkeys(names))
