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
- ``_run_coroutine_sync`` is the module function re-exported by the facade;
  verified unpatched anywhere.
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
import json
import logging
import os
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
from .task_verification import VerificationFinalization

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..deep_think_agent import DeepThinkAgent

logger = logging.getLogger(__name__)


def _facade() -> Any:
    """Late-bound plan_executor facade module (monkeypatch-friendly lookups)."""
    from . import plan_executor

    return plan_executor


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

        deep_think_agent = _facade().DeepThinkAgent(
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
