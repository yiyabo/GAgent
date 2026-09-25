"""Background execution jobs and post-execution audit-repair orchestration."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from app.services.plans.audit_repair_loop import AuditRepairLoopConfig
from app.services.plans.decomposition_jobs import (
    execute_decomposition_job,
    reset_current_job,
    set_current_job,
)
from app.services.plans.dependency_enrichment import check_artifact_readiness
from app.services.plans.plan_executor import ExecutionConfig

from .dependency_plan import (
    _build_dependency_block_details,
    _dependency_warning_step,
    _persist_dependency_block,
)
from .state import logger


def _facade() -> Any:
    """Return the package facade module (late-bound, patch-safe)."""
    from .. import plan_routes as facade

    return facade


def _run_task_audit_repair_after_execution(
    *,
    plan_id: int,
    task_id: int,
    result_status: Any,
    session_id: Optional[str],
    owner_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Audit one task after execution and repair safe artifact/path failures.

    The audit-repair service owns classification and decides whether to rerun,
    delegate to the code agent, or block. This wrapper keeps full-plan sync and
    async execution paths consistent.
    """
    trigger_status = str(result_status or "").strip().lower()
    try:
        loop_result = _facade()._audit_repair_loop_service.run_task_loop(
            plan_id=plan_id,
            task_id=task_id,
            config=AuditRepairLoopConfig(
                max_loops=2,
                max_task_repairs=1,
                enable_delegate_repair=True,
                enable_rerun=True,
                session_id=session_id,
                owner_id=owner_id,
            ),
        )
    except Exception as exc:
        logger.exception("Task audit-repair loop failed for plan %s task %s", plan_id, task_id)
        return {
            "success": trigger_status == "completed",
            "final_status": "completed" if trigger_status == "completed" else "failed",
            "classification": "audit_repair_error",
            "message": str(exc),
            "steps": [],
            "trigger_status": trigger_status,
        }
    data = loop_result.to_dict()
    data["trigger_status"] = trigger_status
    return data


def _status_after_audit_repair(raw_status: Any, repair_result: Dict[str, Any]) -> str:
    if repair_result.get("success") is True:
        return "completed"
    final_status = str(repair_result.get("final_status") or "").strip().lower()
    if final_status:
        return final_status
    return str(raw_status or "failed").strip().lower() or "failed"


def _publish_deliverables_after_audit_repair(
    *,
    plan_id: int,
    task_id: int,
    session_id: Optional[str],
) -> None:
    """Publish contract deliverables when audit repair promotes a task to completed."""
    if not session_id:
        return
    try:
        tree = _facade()._plan_repo.get_plan_tree(plan_id)
        node = tree.nodes.get(task_id)
        if node is None:
            return
        payload: Any = node.execution_result
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                return
        if not isinstance(payload, dict):
            return
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            return
        published = metadata.get("published_artifacts")
        if not isinstance(published, dict) or not published:
            return
        _facade()._plan_executor._publish_contract_deliverables(
            plan_id=plan_id,
            node=node,
            published=published,
            session_context={"session_id": session_id},
        )
    except Exception as exc:
        logging.getLogger("app.routers.plan_routes").warning(
            "Failed to publish deliverables after audit repair for plan %s task %s: %s",
            plan_id, task_id, exc,
        )


def _is_terminal_job_status(raw_status: Any) -> bool:
    return str(raw_status or "").strip().lower() in {
        "succeeded",
        "failed",
        "completed",
        "success",
        "done",
        "error",
    }


def _run_decomposition_job(
    job_id: str,
    plan_id: int,
    task_id: Optional[int],
    expand_depth: Optional[int],
    node_budget: Optional[int],
    allow_existing_children: Optional[bool],
) -> None:
    """Background execution wrapper for async decomposition jobs."""
    execute_decomposition_job(
        plan_decomposer=_facade()._plan_decomposer,
        job_id=job_id,
        plan_id=plan_id,
        mode="single_node",
        task_id=task_id,
        expand_depth=expand_depth,
        node_budget=node_budget,
        allow_existing_children=allow_existing_children,
    )


def _run_task_chain_job(
    *,
    job_id: str,
    plan_id: int,
    target_task_id: int,
    task_order: List[int],
    deep_think: bool = True,
    session_id: Optional[str] = None,
    paper_mode: bool = False,
) -> None:
    token = set_current_job(job_id)
    executed: List[int] = []
    failed: List[int] = []
    skipped: List[int] = []
    step_summaries: List[Dict[str, Any]] = []
    total_steps = len(task_order)

    def _build_progress_stats(
        *,
        current_step: Optional[int] = None,
        current_task_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        done_steps = len(executed) + len(failed) + len(skipped)
        progress_percent = 0
        if total_steps > 0:
            progress_percent = int(round((min(done_steps, total_steps) / total_steps) * 100))
            if done_steps < total_steps:
                progress_percent = max(0, min(99, progress_percent))
            else:
                progress_percent = 100

        stats_payload: Dict[str, Any] = {
            "executed": len(executed),
            "failed": len(failed),
            "skipped": len(skipped),
            "done": done_steps,
            "total_steps": total_steps,
            "progress_percent": progress_percent,
        }
        if current_step is not None:
            stats_payload["current_step"] = current_step
        if current_task_id is not None:
            stats_payload["current_task_id"] = current_task_id
        return stats_payload

    def _publish_progress(
        *,
        current_step: Optional[int] = None,
        current_task_id: Optional[int] = None,
    ) -> None:
        _facade().plan_decomposition_jobs.update_stats(
            job_id,
            _build_progress_stats(
                current_step=current_step,
                current_task_id=current_task_id,
            ),
        )
        _facade().log_job_event(
            "info",
            "Task chain progress update.",
            {
                "sub_type": "task_progress",
                "task_id": current_task_id,
                "step": current_step,
                "total": total_steps,
            },
        )

    try:
        _facade().plan_decomposition_jobs.mark_running(job_id)
        _facade().log_job_event(
            "info",
            "Task chain execution started.",
            {
                "plan_id": plan_id,
                "target_task_id": target_task_id,
                "steps": len(task_order),
                "task_order": task_order,
                "deep_think": deep_think,
                "paper_mode": paper_mode,
            },
        )
        _publish_progress(
            current_step=0,
            current_task_id=task_order[0] if task_order else None,
        )

        session_ctx = {
            "session_id": session_id,
            "user_message": (
                f"Execute task chain for task #{target_task_id} "
                f"(UI-triggered, deep_think={'on' if deep_think else 'off'})."
            ),
            "chat_history": [],
            "recent_tool_results": [],
            "deep_think_enabled": bool(deep_think),
            "paper_mode": bool(paper_mode),
        }

        for idx, task_id in enumerate(task_order, start=1):
            _facade().log_job_event(
                "info",
                "Executing chain step.",
                {
                    "plan_id": plan_id,
                    "target_task_id": target_task_id,
                    "step": idx,
                    "total_steps": len(task_order),
                    "task_id": task_id,
                },
            )
            _publish_progress(current_step=idx, current_task_id=task_id)

            try:
                exec_config = ExecutionConfig(session_context=session_ctx, paper_mode=bool(paper_mode))
                result = _facade()._plan_executor.execute_task(
                    plan_id,
                    task_id,
                    config=exec_config,
                )
            except Exception as exc:  # pragma: no cover - defensive
                failed.append(task_id)
                _publish_progress(current_step=idx, current_task_id=task_id)
                error = f"Task #{task_id} raised an exception: {exc}"
                _facade().log_job_event(
                    "error",
                    "Chain step raised exception; stopping.",
                    {"task_id": task_id, "error": str(exc)},
                )
                _facade().plan_decomposition_jobs.mark_failure(
                    job_id,
                    error,
                    result={
                        "plan_id": plan_id,
                        "target_task_id": target_task_id,
                        "execution_order": task_order,
                        "executed_task_ids": executed,
                        "failed_task_ids": failed,
                        "skipped_task_ids": skipped,
                        "steps": step_summaries,
                    },
                    stats={
                        **_build_progress_stats(
                            current_step=idx,
                            current_task_id=task_id,
                        ),
                    },
                )
                return

            step_summaries.append(
                {
                    "task_id": task_id,
                    "status": result.status,
                    "duration_sec": result.duration_sec,
                }
            )

            if result.status == "completed":
                executed.append(task_id)
                _publish_progress(current_step=idx, current_task_id=task_id)
                continue
            if result.status == "skipped":
                skipped.append(task_id)
                _publish_progress(current_step=idx, current_task_id=task_id)
                error = (
                    f"Task #{task_id} was skipped (likely blocked by dependencies); stopping the chain."
                )
                _facade().log_job_event(
                    "warning",
                    "Chain step skipped; stopping.",
                    {"task_id": task_id, "reason": result.content},
                )
                _facade().plan_decomposition_jobs.mark_failure(
                    job_id,
                    error,
                    result={
                        "plan_id": plan_id,
                        "target_task_id": target_task_id,
                        "execution_order": task_order,
                        "executed_task_ids": executed,
                        "failed_task_ids": failed,
                        "skipped_task_ids": skipped,
                        "steps": step_summaries,
                    },
                    stats={
                        **_build_progress_stats(
                            current_step=idx,
                            current_task_id=task_id,
                        ),
                    },
                )
                return

            failed.append(task_id)
            _publish_progress(current_step=idx, current_task_id=task_id)
            error = f"Task #{task_id} failed; stopping the chain."
            _facade().log_job_event(
                "error",
                "Chain step failed; stopping.",
                {"task_id": task_id, "reason": result.content},
            )
            _facade().plan_decomposition_jobs.mark_failure(
                job_id,
                error,
                result={
                    "plan_id": plan_id,
                    "target_task_id": target_task_id,
                    "execution_order": task_order,
                    "executed_task_ids": executed,
                    "failed_task_ids": failed,
                    "skipped_task_ids": skipped,
                    "steps": step_summaries,
                },
                stats={
                    **_build_progress_stats(
                        current_step=idx,
                        current_task_id=task_id,
                    ),
                },
            )
            return

        _publish_progress(
            current_step=total_steps,
            current_task_id=task_order[-1] if task_order else None,
        )
        _facade().plan_decomposition_jobs.mark_success(
            job_id,
            result={
                "plan_id": plan_id,
                "target_task_id": target_task_id,
                "execution_order": task_order,
                "executed_task_ids": executed,
                "failed_task_ids": failed,
                "skipped_task_ids": skipped,
                "steps": step_summaries,
            },
            stats={
                **_build_progress_stats(
                    current_step=total_steps,
                    current_task_id=task_order[-1] if task_order else None,
                ),
            },
        )
    finally:
        reset_current_job(token)


def _run_full_plan_job(
    *,
    job_id: str,
    plan_id: int,
    task_order: List[int],
    initial_completed_steps: int = 0,
    overall_total_steps: Optional[int] = None,
    deep_think: bool = True,
    session_id: Optional[str] = None,
    owner_id: Optional[str] = None,
    paper_mode: bool = False,
    stop_on_failure: bool = True,
    dependency_block_mode: str = "warn",
) -> None:
    """Background execution wrapper for full-plan TodoList-based execution."""
    token = set_current_job(job_id)
    executed: List[int] = []
    failed: List[int] = []
    skipped: List[int] = []
    step_summaries: List[Dict[str, Any]] = []
    audit_repairs: Dict[str, Any] = {}
    total_steps = len(task_order)
    baseline_completed_steps = max(0, int(initial_completed_steps or 0))
    overall_steps = max(total_steps, int(overall_total_steps or 0), baseline_completed_steps)
    completed_steps = 0

    def _build_progress_stats(
        *,
        current_step: Optional[int] = None,
        current_task_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        done_steps = len(executed) + len(failed) + len(skipped)
        overall_done_steps = min(overall_steps, baseline_completed_steps + completed_steps)
        progress_percent = 0
        if overall_steps > 0:
            progress_percent = int(round((overall_done_steps / overall_steps) * 100))
            if overall_done_steps < overall_steps:
                progress_percent = max(0, min(99, progress_percent))
            else:
                progress_percent = 100
        stats_payload: Dict[str, Any] = {
            "executed": len(executed),
            "failed": len(failed),
            "skipped": len(skipped),
            "done": done_steps,
            "total_steps": total_steps,
            "overall_done_steps": overall_done_steps,
            "overall_total_steps": overall_steps,
            "progress_percent": progress_percent,
        }
        if current_step is not None:
            stats_payload["current_step"] = current_step
        if current_task_id is not None:
            stats_payload["current_task_id"] = current_task_id
        return stats_payload

    def _publish_progress(
        *,
        current_step: Optional[int] = None,
        current_task_id: Optional[int] = None,
    ) -> None:
        _facade().plan_decomposition_jobs.update_stats(job_id, _build_progress_stats(
            current_step=current_step, current_task_id=current_task_id,
        ))
        _facade().log_job_event(
            "info",
            "Full plan progress update.",
            {
                "sub_type": "task_progress",
                "task_id": current_task_id,
                "step": current_step,
                "total": total_steps,
            },
        )

    try:
        _facade().plan_decomposition_jobs.mark_running(job_id)
        _facade().log_job_event(
            "info",
            "Full plan execution started.",
            {
                "plan_id": plan_id,
                "steps": len(task_order),
                "overall_total_steps": overall_steps,
                "initial_completed_steps": baseline_completed_steps,
                "task_order": task_order,
                "deep_think": deep_think,
                "paper_mode": paper_mode,
                "stop_on_failure": stop_on_failure,
            },
        )
        _publish_progress(current_step=0, current_task_id=task_order[0] if task_order else None)

        session_ctx = {
            "session_id": session_id,
            "user_message": f"Execute full plan (plan_id={plan_id}, deep_think={'on' if deep_think else 'off'}).",
            "chat_history": [],
            "recent_tool_results": [],
            "deep_think_enabled": bool(deep_think),
            "paper_mode": bool(paper_mode),
        }

        for idx, task_id in enumerate(task_order, start=1):
            _facade().log_job_event(
                "info",
                "Executing plan step.",
                {"plan_id": plan_id, "step": idx, "total_steps": total_steps, "task_id": task_id},
            )
            _publish_progress(current_step=idx, current_task_id=task_id)

            if _facade().plan_decomposition_jobs.is_execution_paused(job_id):
                _facade().log_job_event(
                    "info",
                    "Plan execution paused; waiting before dispatching next task.",
                    {"plan_id": plan_id, "step": idx, "total_steps": total_steps, "task_id": task_id},
                )
                if not _facade().plan_decomposition_jobs.wait_while_paused(job_id):
                    _facade().log_job_event(
                        "error",
                        "Plan execution job state lost while paused; aborting remaining tasks.",
                        {"plan_id": plan_id, "step": idx, "task_id": task_id},
                    )
                    skipped.extend(task_order[idx - 1:])
                    break
                _facade().log_job_event(
                    "info",
                    "Plan execution resumed; continuing dispatch.",
                    {"plan_id": plan_id, "step": idx, "total_steps": total_steps, "task_id": task_id},
                )

            # Re-check task status at execution time so background runs do not
            # re-execute work that finished after the queue was created.
            try:
                current_tree = _facade()._plan_repo.get_plan_tree(plan_id)

                # Safety guard: skip composite parent tasks — only leaf tasks should be executed
                if current_tree.children_ids(task_id):
                    _facade().log_job_event(
                        "info",
                        "Skipping composite parent task.",
                        {"plan_id": plan_id, "task_id": task_id, "step": idx, "reason": "has_children"},
                    )
                    executed.append(task_id)
                    completed_steps += 1
                    step_summaries.append({"task_id": task_id, "status": "composite_skipped", "duration_sec": 0.0})
                    _publish_progress(current_step=idx, current_task_id=task_id)
                    continue

                current_state_by_task = _facade()._resolve_effective_task_states(
                    plan_id,
                    current_tree,
                    snapshot=_facade()._build_plan_execution_snapshot(
                        plan_id,
                        exclude_job_ids={job_id},
                    ),
                )
                current_status = str(
                    (current_state_by_task.get(task_id) or {}).get("effective_status") or "pending"
                ).strip().lower()
                if current_status in ("completed", "running", "delegating"):
                    already_status = "already_running" if current_status == "running" else "already_completed"
                    executed.append(task_id)
                    if current_status != "running":
                        completed_steps += 1
                    step_summaries.append(
                        {
                            "task_id": task_id,
                            "status": already_status,
                            "duration_sec": 0.0,
                        }
                    )
                    _facade().log_job_event(
                        "info",
                        (
                            "Plan step already running; skipping execution."
                            if current_status == "running"
                            else "Plan step already completed; skipping execution."
                        ),
                        {
                            "plan_id": plan_id,
                            "task_id": task_id,
                            "step": idx,
                            "status": current_status,
                        },
                    )
                    _publish_progress(current_step=idx, current_task_id=task_id)
                    continue
                dependency_block = _build_dependency_block_details(
                    current_tree,
                    task_id,
                    state_by_task=current_state_by_task,
                )
                if dependency_block is not None:
                    if str(dependency_block_mode or "warn").strip().lower() == "block":
                        _persist_dependency_block(plan_id, task_id, dependency_block)
                        skipped.append(task_id)
                        step_summaries.append(
                            {
                                "task_id": task_id,
                                "status": "blocked_by_dependencies",
                                "duration_sec": 0.0,
                                "reason": dependency_block["reason"],
                                "metadata": dependency_block["metadata"],
                            }
                        )
                    else:
                        step_summaries.append(_dependency_warning_step(task_id, dependency_block))
                    _facade().log_job_event(
                        "warning",
                        (
                            "Plan step blocked by incomplete dependencies."
                            if str(dependency_block_mode or "warn").strip().lower() == "block"
                            else "Plan step has incomplete dependency warning; continuing."
                        ),
                        {
                            "plan_id": plan_id,
                            "task_id": task_id,
                            "step": idx,
                            "incomplete_dependencies": dependency_block["metadata"]["incomplete_dependencies"],
                            "reason": dependency_block["reason"],
                        },
                    )
                    _publish_progress(current_step=idx, current_task_id=task_id)
                    if str(dependency_block_mode or "warn").strip().lower() == "block" and stop_on_failure:
                        error = f"Task #{task_id} was blocked by incomplete dependencies; stopping chain."
                        _facade().plan_decomposition_jobs.mark_failure(
                            job_id,
                            error,
                            result={
                                "plan_id": plan_id,
                                "execution_order": task_order,
                                "executed_task_ids": executed,
                                "failed_task_ids": failed,
                                "skipped_task_ids": skipped,
                                "steps": step_summaries,
                            },
                            stats=_build_progress_stats(current_step=idx, current_task_id=task_id),
                        )
                        return
                    if str(dependency_block_mode or "warn").strip().lower() == "block":
                        continue
            except Exception as exc:
                # Transient tree-refresh failure: retry once after a short
                # backoff before giving up on this step.  This avoids burning
                # through the queue on a momentary DB hiccup.
                import time as _time
                _time.sleep(1.0)
                try:
                    current_tree = _facade()._plan_repo.get_plan_tree(plan_id)
                    _facade().log_job_event(
                        "info",
                        "Plan tree refresh succeeded on retry.",
                        {"plan_id": plan_id, "task_id": task_id, "step": idx},
                    )
                except Exception as retry_exc:
                    _facade().log_job_event(
                        "error",
                        "Plan tree refresh failed on retry; aborting remaining steps.",
                        {"plan_id": plan_id, "task_id": task_id, "step": idx, "error": str(retry_exc)},
                    )
                    failed.append(task_id)
                    step_summaries.append({
                        "task_id": task_id,
                        "status": "tree_refresh_failed",
                        "duration_sec": 0.0,
                        "error": str(retry_exc),
                    })
                    _publish_progress(current_step=idx, current_task_id=task_id)
                    # Without a valid tree, dependency/completion checks for
                    # subsequent tasks are unreliable.  Abort the entire job
                    # regardless of stop_on_failure to avoid cascading skips.
                    error = (
                        f"Task #{task_id}: plan tree refresh failed after retry ({retry_exc}); "
                        "aborting job — remaining tasks cannot be safely checked."
                    )
                    _facade().plan_decomposition_jobs.mark_failure(
                        job_id,
                        error,
                        result={
                            "plan_id": plan_id,
                            "execution_order": task_order,
                            "executed_task_ids": executed,
                            "failed_task_ids": failed,
                            "skipped_task_ids": skipped,
                            "steps": step_summaries,
                            "audit_repairs": audit_repairs,
                        },
                        stats=_build_progress_stats(current_step=idx, current_task_id=task_id),
                    )
                    return

                # Retry succeeded — re-run the same state checks that the
                # primary try block performs (composite parent, already
                # completed/running, dependency-blocked).
                if current_tree.children_ids(task_id):
                    _facade().log_job_event(
                        "info",
                        "Skipping composite parent task (after retry).",
                        {"plan_id": plan_id, "task_id": task_id, "step": idx, "reason": "has_children"},
                    )
                    executed.append(task_id)
                    completed_steps += 1
                    step_summaries.append({"task_id": task_id, "status": "composite_skipped", "duration_sec": 0.0})
                    _publish_progress(current_step=idx, current_task_id=task_id)
                    continue

                current_state_by_task = _facade()._resolve_effective_task_states(
                    plan_id,
                    current_tree,
                    snapshot=_facade()._build_plan_execution_snapshot(plan_id, exclude_job_ids={job_id}),
                )
                current_status = str(
                    (current_state_by_task.get(task_id) or {}).get("effective_status") or "pending"
                ).strip().lower()
                if current_status in ("completed", "running", "delegating"):
                    already_status = "already_running" if current_status == "running" else "already_completed"
                    executed.append(task_id)
                    if current_status != "running":
                        completed_steps += 1
                    step_summaries.append({"task_id": task_id, "status": already_status, "duration_sec": 0.0})
                    _publish_progress(current_step=idx, current_task_id=task_id)
                    continue

                dependency_block = _build_dependency_block_details(
                    current_tree, task_id, state_by_task=current_state_by_task,
                )
                if dependency_block is not None:
                    if str(dependency_block_mode or "warn").strip().lower() == "block":
                        _persist_dependency_block(plan_id, task_id, dependency_block)
                        skipped.append(task_id)
                        step_summaries.append({
                            "task_id": task_id,
                            "status": "blocked_by_dependencies",
                            "duration_sec": 0.0,
                            "reason": dependency_block["reason"],
                            "metadata": dependency_block["metadata"],
                        })
                    else:
                        step_summaries.append(_dependency_warning_step(task_id, dependency_block))
                    _publish_progress(current_step=idx, current_task_id=task_id)
                    if str(dependency_block_mode or "warn").strip().lower() == "block" and stop_on_failure:
                        error = f"Task #{task_id} was blocked by incomplete dependencies; stopping chain."
                        _facade().plan_decomposition_jobs.mark_failure(
                            job_id, error,
                            result={
                                "plan_id": plan_id, "execution_order": task_order,
                                "executed_task_ids": executed, "failed_task_ids": failed,
                                "skipped_task_ids": skipped, "steps": step_summaries,
                                "audit_repairs": audit_repairs,
                            },
                            stats=_build_progress_stats(current_step=idx, current_task_id=task_id),
                        )
                        return
                    if str(dependency_block_mode or "warn").strip().lower() == "block":
                        continue

            try:
                # --- Artifact readiness guard ---
                _readiness_block = check_artifact_readiness(
                    current_tree.nodes.get(task_id),
                    current_tree,
                    manifest=None,
                    state_by_task=current_state_by_task,
                )
                if _readiness_block is not None:
                    _facade().log_job_event(
                        "warning",
                        (
                            "Task blocked by missing input artifacts."
                            if str(dependency_block_mode or "warn").strip().lower() == "block"
                            else "Task has missing input artifact warning; continuing."
                        ),
                        {"task_id": task_id, "step": idx, "reason": _readiness_block.reason},
                    )
                    step_summaries.append({
                        "task_id": task_id,
                        "status": (
                            "missing_input_artifact"
                            if str(dependency_block_mode or "warn").strip().lower() == "block"
                            else "missing_input_artifact_warning"
                        ),
                        "duration_sec": 0.0,
                        "reason": _readiness_block.reason,
                        "metadata": {
                            "dependency_warning": str(dependency_block_mode or "warn").strip().lower() != "block",
                            "degraded_input": str(dependency_block_mode or "warn").strip().lower() != "block",
                        },
                    })
                    _publish_progress(current_step=idx, current_task_id=task_id)
                    if str(dependency_block_mode or "warn").strip().lower() == "block":
                        skipped.append(task_id)
                        continue

                exec_config = ExecutionConfig(
                    session_context=session_ctx,
                    paper_mode=bool(paper_mode),
                    enforce_dependencies=str(dependency_block_mode or "warn").strip().lower() == "block",
                )
                
                # Task-level retry loop: up to 3 attempts for failed tasks
                max_task_attempts = 3
                result = None
                for task_attempt in range(1, max_task_attempts + 1):
                    result = _facade()._plan_executor.execute_task(plan_id, task_id, config=exec_config)
                    
                    if result.status == "completed":
                        break
                    
                    if task_attempt == max_task_attempts:
                        _facade().log_job_event(
                            "warning",
                            f"Task #{task_id} failed after {max_task_attempts} attempts; proceeding to audit repair.",
                            {
                                "task_id": task_id,
                                "step": idx,
                                "attempts": task_attempt,
                                "final_status": result.status,
                            },
                        )
                        break
                    
                    _facade().log_job_event(
                        "warning",
                        f"Task #{task_id} failed (attempt {task_attempt}/{max_task_attempts}); retrying.",
                        {
                            "task_id": task_id,
                            "step": idx,
                            "attempt": task_attempt,
                            "status": result.status,
                            "content": result.content[:200] if result.content else None,
                        },
                    )
            except Exception as exc:
                repair_result: Optional[Dict[str, Any]] = None
                failure_payload = {
                    "status": "failed",
                    "content": f"Task execution raised an exception: {exc}",
                    "metadata": {"execution_exception": True, "error": str(exc)},
                }
                try:
                    _facade()._plan_repo.update_task(
                        plan_id,
                        task_id,
                        status="failed",
                        execution_result=json.dumps(failure_payload, ensure_ascii=False),
                    )
                    repair_result = _facade()._run_task_audit_repair_after_execution(
                        plan_id=plan_id,
                        task_id=task_id,
                        result_status="failed",
                        session_id=session_id,
                        owner_id=owner_id,
                    )
                    audit_repairs[str(task_id)] = repair_result
                except Exception as repair_exc:
                    audit_repairs[str(task_id)] = {"success": False, "error": str(repair_exc), "trigger_status": "failed"}
                if repair_result is not None and _status_after_audit_repair("failed", repair_result) == "completed":
                    executed.append(task_id)
                    completed_steps += 1
                    _publish_deliverables_after_audit_repair(
                        plan_id=plan_id, task_id=task_id, session_id=session_id,
                    )
                    step_summaries.append(
                        {
                            "task_id": task_id,
                            "status": "completed",
                            "duration_sec": None,
                            "audit_repair": repair_result,
                        }
                    )
                    _publish_progress(current_step=idx, current_task_id=task_id)
                    continue
                failed.append(task_id)
                step_summaries.append(
                    {
                        "task_id": task_id,
                        "status": "exception",
                        "duration_sec": None,
                        "error": str(exc),
                        "audit_repair": audit_repairs.get(str(task_id)),
                    }
                )
                _publish_progress(current_step=idx, current_task_id=task_id)
                error = f"Task #{task_id} raised an exception: {exc}"
                if stop_on_failure:
                    _facade().log_job_event("error", "Plan step raised exception.", {"task_id": task_id, "error": str(exc)})
                    _facade().plan_decomposition_jobs.mark_failure(
                        job_id, error,
                        result={"plan_id": plan_id, "execution_order": task_order, "executed_task_ids": executed, "failed_task_ids": failed, "skipped_task_ids": skipped, "steps": step_summaries, "audit_repairs": audit_repairs},
                        stats=_build_progress_stats(current_step=idx, current_task_id=task_id),
                    )
                    return
                _facade().log_job_event(
                    "warning",
                    "Plan step raised exception; continuing.",
                    {"task_id": task_id, "error": str(exc)},
                )
                continue

            repair_result = _facade()._run_task_audit_repair_after_execution(
                plan_id=plan_id,
                task_id=task_id,
                result_status=result.status,
                session_id=session_id,
                owner_id=owner_id,
            )
            audit_repairs[str(task_id)] = repair_result
            final_status = _status_after_audit_repair(result.status, repair_result)

            step_summaries.append({"task_id": task_id, "status": final_status, "duration_sec": result.duration_sec, "audit_repair": repair_result})

            _facade().log_job_event(
                "info" if result.status == "completed" else "warning" if result.status == "skipped" else "error",
                f"Plan step completed: task #{task_id}",
                {
                    "sub_type": "step_complete",
                    "plan_id": plan_id,
                    "task_id": task_id,
                    "step": idx,
                    "total_steps": total_steps,
                    "status": final_status,
                    "duration_sec": result.duration_sec,
                },
            )

            if final_status == "completed":
                executed.append(task_id)
                completed_steps += 1
                _publish_progress(current_step=idx, current_task_id=task_id)
                continue
            if final_status in {"skipped", "blocked"}:
                skipped.append(task_id)
                _publish_progress(current_step=idx, current_task_id=task_id)
                if stop_on_failure:
                    error = f"Task #{task_id} was {final_status}; stopping chain."
                    _facade().log_job_event("warning", "Plan step skipped; stopping.", {"task_id": task_id, "reason": result.content})
                    _facade().plan_decomposition_jobs.mark_failure(
                        job_id, error,
                        result={"plan_id": plan_id, "execution_order": task_order, "executed_task_ids": executed, "failed_task_ids": failed, "skipped_task_ids": skipped, "steps": step_summaries, "audit_repairs": audit_repairs},
                        stats=_build_progress_stats(current_step=idx, current_task_id=task_id),
                    )
                    return
                continue

            failed.append(task_id)
            _publish_progress(current_step=idx, current_task_id=task_id)
            if stop_on_failure:
                error = f"Task #{task_id} failed; stopping chain."
                _facade().log_job_event("error", "Plan step failed; stopping.", {"task_id": task_id, "reason": result.content})
                _facade().plan_decomposition_jobs.mark_failure(
                    job_id, error,
                    result={"plan_id": plan_id, "execution_order": task_order, "executed_task_ids": executed, "failed_task_ids": failed, "skipped_task_ids": skipped, "steps": step_summaries, "audit_repairs": audit_repairs},
                    stats=_build_progress_stats(current_step=idx, current_task_id=task_id),
                )
                return
            _facade().log_job_event("warning", "Plan step failed; continuing.", {"task_id": task_id, "reason": result.content})

        _publish_progress(current_step=total_steps, current_task_id=task_order[-1] if task_order else None)
        final_result = {
            "plan_id": plan_id,
            "execution_order": task_order,
            "executed_task_ids": executed,
            "failed_task_ids": failed,
            "skipped_task_ids": skipped,
            "steps": step_summaries,
            "audit_repairs": audit_repairs,
        }
        final_stats = _build_progress_stats(
            current_step=total_steps,
            current_task_id=task_order[-1] if task_order else None,
        )
        if failed or skipped:
            _facade().plan_decomposition_jobs.mark_failure(
                job_id,
                f"Full plan execution finished with {len(failed)} failed and {len(skipped)} skipped task(s).",
                result=final_result,
                stats=final_stats,
            )
        else:
            _facade().plan_decomposition_jobs.mark_success(
                job_id,
                result=final_result,
                stats=final_stats,
            )
    finally:
        reset_current_job(token)
