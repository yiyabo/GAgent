"""Plan-operation dispatch cluster of ``action_handlers``.

Moved out of ``action_handlers.py`` per
design/2026-09-24-backend-godfiles-refactor-plan.md §4.7 (handlers cluster ④):
the plan-generation readiness helpers, ``handle_plan_action`` (create/list/
execute/delete/review/optimize sub-dispatch), the rerun-task-execution helper
family and the async rerun wrapper ``handle_task_action_async``.

Sanctioned deviations (all single lines, listed for review):
- ``_ah()`` late binding for names that are patched **on the action_handlers
  namespace** — ``_set_session_plan_id`` (string-path patch,
  app/tests/plan/test_plan_generation_pipeline.py:350) and
  ``plan_decomposition_jobs`` (app/tests/chat/test_rerun_task_dispatch.py:124;
  bound once per function as a local alias so the call expressions stay
  verbatim).
- ``_ah()`` late binding for facade-resident helpers that stay behind:
  ``_coerce_plan_description``, ``_extract_explicit_plan_tasks_from_goal``
  (goal→seed-task parsing kept in the facade this phase) and
  ``handle_unknown_action`` / ``handle_task_action`` (fallbacks defined in the
  facade; the latter moves to ``action_task_ops`` in cluster ⑤ and stays
  reachable through the facade).
- ``_RERUN_TASK_EXECUTION_JOB_KEY`` moved here and re-exported by the facade.

The module uses its own ``logging.getLogger(__name__)`` (split precedent); every
log message and AgentStep payload literal is unchanged.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from app.services.plans.decomposition_jobs import reset_current_job, set_current_job
from app.services.plans.plan_decomposer import PlanDecomposer
from app.services.plans.plan_executor import ExecutionConfig
from app.services.plans.plan_generation import (
    create_plan_and_generate,
    ensure_plan_generation_ready,
)
from app.services.plans.plan_optimizer import (
    auto_optimize_plan,
    capture_plan_optimization_outcome,
)
from app.services.plans.plan_models import PlanTree

from .action_phagescope import _build_phagescope_research_seed_tasks
from .models import AgentStep
from app.services.llm.structured_response import LLMAction

logger = logging.getLogger(__name__)

_RERUN_TASK_EXECUTION_JOB_KEY = "_rerun_task_execution_job_id"


def _ah() -> Any:
    """Late-bound action_handlers facade module (monkeypatch-friendly lookups)."""
    from . import action_handlers

    return action_handlers


def _build_plan_generation_session_context(agent: Any) -> Dict[str, Any]:
    context: Dict[str, Any] = {
        "session_id": getattr(agent, "session_id", None),
        "user_message": getattr(agent, "_current_user_message", None),
        "chat_history": getattr(agent, "history", None),
        "recent_tool_results": (getattr(agent, "extra_context", {}) or {}).get("recent_tool_results", []),
        "owner_id": (getattr(agent, "extra_context", {}) or {}).get("owner_id"),
    }
    return {key: value for key, value in context.items() if value is not None}


async def _maybe_ensure_plan_generation_ready_for_agent(
    agent: Any,
    *,
    plan_id: int,
    fallback_tree: Any,
) -> Any:
    repo = getattr(getattr(agent, "plan_session", None), "repo", None)
    if repo is None or not callable(getattr(repo, "get_plan_tree", None)):
        return SimpleNamespace(plan_tree=fallback_tree, decomposition_status="unknown")
    return await ensure_plan_generation_ready(
        plan_id=plan_id,
        repo=repo,
        decomposer=agent.plan_decomposer or PlanDecomposer(repo=repo),
        session_context=_build_plan_generation_session_context(agent),
    )


def _artifact_preflight_failure_step(
    *,
    action: LLMAction,
    plan_id: int,
    decomposition_status: Optional[str],
    preflight_result: Any,
) -> AgentStep:
    return AgentStep(
        action=action,
        success=False,
        message=preflight_result.summary(),
        details={
            "plan_id": plan_id,
            "decomposition_status": decomposition_status,
            "preflight": preflight_result.model_dump(),
            "status": "artifact_preflight_failed",
        },
    )


def _should_run_artifact_preflight(tree: Any) -> bool:
    return isinstance(getattr(tree, "nodes", None), dict)


async def handle_plan_action(agent: Any, action: LLMAction) -> AgentStep:
    params = action.parameters or {}
    if action.name == "create_plan":
        title = params.get("title")
        goal = params.get("goal")
        if not title:
            if isinstance(goal, str) and goal.strip():
                title = goal.strip()[:80]
            else:
                title = f"Plan-{agent.conversation_id or 'new'}"
        description = _ah()._coerce_plan_description(params.get("description"), goal)
        owner = params.get("owner")
        if not owner:
            owner = agent.extra_context.get("owner_id")
        metadata = params.get("metadata")
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        # Ensure plan origin is recorded for later comparison (standard vs deepthink).
        metadata.setdefault("plan_origin", "standard")
        metadata.setdefault("created_by", "structured_agent")
        raw_tasks = params.get("tasks")
        seed_tasks = raw_tasks if isinstance(raw_tasks, list) else _ah()._extract_explicit_plan_tasks_from_goal(goal)
        if not seed_tasks:
            seed_tasks = _build_phagescope_research_seed_tasks(goal)
            if seed_tasks:
                metadata.setdefault("plan_seed_source", "phagescope_research_seed_plan")
                metadata.setdefault("skip_auto_decomposition", True)
        generation = await create_plan_and_generate(
            title=title,
            description=description,
            tasks=seed_tasks if isinstance(seed_tasks, list) and seed_tasks else None,
            owner=owner,
            metadata=metadata,
            repo=agent.plan_session.repo,
            decomposer=agent.plan_decomposer or PlanDecomposer(repo=agent.plan_session.repo),
            session_context=_build_plan_generation_session_context(agent),
        )
        new_tree = generation.plan_tree
        created_seed_tasks = list(generation.seeded_tasks)

        # Bind session to the new plan and refresh the in-memory tree so that
        # any seed tasks are immediately visible to the caller and UI.
        agent.plan_session.bind(new_tree.id)
        agent._refresh_plan_tree(force_reload=True)
        effective_tree = agent.plan_tree or new_tree
        agent.plan_tree = effective_tree
        agent.extra_context["plan_id"] = effective_tree.id
        
        # Persist the plan binding to the database immediately
        if agent.session_id:
            _ah()._set_session_plan_id(agent.session_id, effective_tree.id, owner_id=owner)
        auto_review_payload = dict(generation.auto_review or {})
        if auto_review_payload:
            agent._refresh_plan_tree(force_reload=True)
            effective_tree = agent.plan_tree or agent.plan_session.repo.get_plan_tree(effective_tree.id)
            agent.plan_tree = effective_tree

        message = f'Created and bound new plan #{effective_tree.id} "{effective_tree.title}".'
        if created_seed_tasks:
            message += f" Seeded with {len(created_seed_tasks)} top-level task(s) from the proposed plan."
        if generation.decomposition_status == "completed":
            message += " Integrated decomposition completed before returning the plan."
        elif generation.decomposition_status == "partial":
            message += " Integrated decomposition completed partially; inspect failed nodes before execution."
        if isinstance(auto_review_payload, dict):
            if isinstance(auto_review_payload.get("auto_optimize"), dict):
                message += " Automatic review found improvements and optimized the plan."
            elif auto_review_payload.get("success"):
                message += " Automatic review completed."
        details = {
            "plan_id": effective_tree.id,
            "title": effective_tree.title,
            "task_count": effective_tree.node_count(),
            "root_task_id": generation.root_task_id,
            "decomposition_status": generation.decomposition_status,
            "decomposition_completed": generation.decomposition_status == "completed",
            "material_collection": {
                "used": bool(generation.collected_materials),
                "count": len(generation.collected_materials),
                "entries": generation.collected_materials,
            },
        }
        if auto_review_payload:
            details["auto_review"] = auto_review_payload
            if isinstance(auto_review_payload.get("auto_optimize"), dict):
                details["auto_optimize"] = auto_review_payload["auto_optimize"]
        if created_seed_tasks:
            details["seed_tasks"] = [node.model_dump() for node in created_seed_tasks]
        if generation.decomposition is not None:
            details["decomposition"] = {
                "created": [node.model_dump() for node in generation.decomposition.created_tasks],
                "failed_nodes": generation.decomposition.failed_nodes,
                "stopped_reason": generation.decomposition.stopped_reason,
                "stats": generation.decomposition.stats,
            }
        agent._dirty = True

        return AgentStep(
            action=action, success=True, message=message, details=details
        )

    if action.name == "list_plans":
        plans = agent.plan_session.list_plans()
        details = {"plans": [plan.model_dump() for plan in plans]}
        message = "Available plans have been listed." if plans else "No plans are currently available."
        return AgentStep(
            action=action, success=True, message=message, details=details
        )

    if action.name in ("execute_plan", "execute_all"):
        # LLM output frequently emits name='execute_all' (the prompt catalog
        # and tool_schemas document it as an operation value); route it to the
        # same full-plan background execution as execute_plan.
        tree = agent._require_plan_bound()
        if agent.plan_executor is None:
            raise ValueError("Plan executor is not enabled in this environment.")
        paper_mode_raw = params.get("paper_mode")
        paper_mode = False
        if isinstance(paper_mode_raw, bool):
            paper_mode = paper_mode_raw
        elif paper_mode_raw is not None:
            paper_mode = str(paper_mode_raw).strip().lower() in {"1", "true", "yes", "on", "y"}
        # Build session context and pass to plan executor.
        session_ctx = {
            "session_id": agent.session_id,  # For tool calls.
            "user_message": agent._current_user_message if hasattr(agent, "_current_user_message") else None,
            "chat_history": agent.history,
            "chat_history_max_messages": getattr(agent, "max_history_messages", 80),
            "recent_tool_results": agent.extra_context.get("recent_tool_results", []),
            "paper_mode": paper_mode,
        }
        exec_config = ExecutionConfig(session_context=session_ctx, paper_mode=paper_mode)
        summary = await asyncio.to_thread(agent.plan_executor.execute_plan, tree.id, config=exec_config)
        executed_count = len(summary.executed_task_ids)
        failed_count = len(summary.failed_task_ids)
        skipped_count = len(summary.skipped_task_ids)
        parts = [f"Plan #{tree.id} finished execution"]
        parts.append(f"Succeeded tasks: {executed_count}")
        if failed_count:
            parts.append(f"Failed tasks: {failed_count}")
        if skipped_count:
            parts.append(f"Skipped tasks: {skipped_count}")
        message = "，".join(parts) + "。"
        details = summary.to_dict()
        success = failed_count == 0 and skipped_count == 0
        agent._refresh_plan_tree(force_reload=True)
        return AgentStep(
            action=action, success=success, message=message, details=details
        )

    if action.name == "delete_plan":
        plan_id_param = params.get("plan_id") or agent.plan_session.plan_id
        plan_id = agent._coerce_int(plan_id_param, "plan_id")
        agent.plan_session.repo.delete_plan(plan_id)
        detached = False
        if agent.plan_session.plan_id == plan_id:
            agent.plan_session.detach()
            agent.plan_tree = None
            agent.extra_context.pop("plan_id", None)
            detached = True
        agent._dirty = False
        message = f"Plan #{plan_id} has been deleted."
        details = {"plan_id": plan_id, "detached": detached}
        return AgentStep(
            action=action, success=True, message=message, details=details
        )

    if action.name == "review_plan":
        tree = agent._require_plan_bound()
        plan_id = tree.id
        from app.services.plans.plan_rubric_evaluator import (
            evaluate_plan_rubric,
            is_rubric_evaluation_unavailable,
        )
        readiness = await _maybe_ensure_plan_generation_ready_for_agent(
            agent,
            plan_id=plan_id,
            fallback_tree=tree,
        )
        tree = readiness.plan_tree
        # NOTE: review skips artifact preflight — reviewing a plan with
        # broken artifact contracts is fine; the review should report issues.
        try:
            rubric_result = await asyncio.to_thread(
                evaluate_plan_rubric,
                tree,
                evaluator_provider="qwen",
                evaluator_model="qwen3.7-max",
                model_provider=(agent.extra_context or {}).get("model_provider"),
            )
        except Exception as exc:
            logger.warning("review_plan rubric evaluation failed: %s", exc)
            return AgentStep(
                action=action, success=False,
                message=f"Rubric evaluation failed: {exc}",
                details={"plan_id": plan_id},
            )
        # Persist evaluation into plan metadata
        merged_meta = dict(getattr(tree, "metadata", None) or {})
        merged_meta["plan_evaluation"] = rubric_result.to_dict()
        # Sync plan_optimization.overall_score_after with latest review score
        existing_optimization = merged_meta.get("plan_optimization")
        if isinstance(existing_optimization, dict):
            existing_optimization["overall_score_after"] = rubric_result.overall_score
            merged_meta["plan_optimization"] = existing_optimization
        try:
            agent.plan_session.repo.update_plan_metadata(plan_id, merged_meta)
        except Exception as meta_exc:
            logger.warning("Failed to persist plan rubric evaluation: %s", meta_exc)
        agent._refresh_plan_tree(force_reload=True)
        rubric_unavailable = is_rubric_evaluation_unavailable(rubric_result)
        message = (
            f"Plan #{plan_id} review unavailable. Rubric evaluator could not complete."
            if rubric_unavailable
            else (
                f"Plan #{plan_id} review complete. "
                f"Rubric score: {rubric_result.overall_score:.1f}/100.\n"
                "INSTRUCTIONS FOR PRESENTING THE REVIEW:\n"
                "1. Show the overall score prominently.\n"
                "2. List each dimension score in a table (dimension name, score, brief assessment).\n"
                "3. For dimensions scoring below 70, explain the specific problems found.\n"
                "4. Provide concrete, actionable improvement suggestions for each weak dimension.\n"
                "5. If the user requested autonomous completion, continue with optimize_plan when the rubric indicates concrete improvements.\n"
                "6. Otherwise, present the review and ask whether they want deeper optimization or discussion."
            )
        )
        details = {
            "plan_id": plan_id,
            "plan_title": tree.title,
            "decomposition_status": readiness.decomposition_status,
            "status": "evaluation_unavailable" if rubric_unavailable else "completed",
            "rubric_score": rubric_result.overall_score,
            "rubric_dimension_scores": rubric_result.dimension_scores,
            "rubric_subcriteria_scores": rubric_result.subcriteria_scores,
            "rubric_feedback": rubric_result.feedback,
            "rubric_evaluator": {
                "provider": rubric_result.evaluator_provider,
                "model": rubric_result.evaluator_model,
                "rubric_version": rubric_result.rubric_version,
                "evaluated_at": rubric_result.evaluated_at,
            },
            "degraded": rubric_unavailable,
        }
        return AgentStep(
            action=action, success=not rubric_unavailable, message=message, details=details
        )

    if action.name == "optimize_plan":
        tree = agent._require_plan_bound()
        plan_id = tree.id
        changes = params.get("changes")

        repo = agent.plan_session.repo
        readiness = await _maybe_ensure_plan_generation_ready_for_agent(
            agent,
            plan_id=plan_id,
            fallback_tree=tree,
        )
        # Use the tree from readiness (may have been mutated/expanded)
        tree = getattr(readiness, "plan_tree", None) or tree
        # NOTE: optimize skips artifact preflight — the whole point of
        # optimize is to fix issues including broken artifact contracts.
        if not changes or not isinstance(changes, list):
            outcome = await auto_optimize_plan(
                plan_id=plan_id,
                repo=repo,
                model_provider=(agent.extra_context or {}).get("model_provider"),
            )
            review_before = outcome.review_before
            review_after = outcome.review_after or review_before
            score_delta = None
            if review_before is not None and review_after is not None:
                score_delta = float(review_after.overall_score) - float(review_before.overall_score)
            success = bool(outcome.applied_changes) or not outcome.optimization_needed
            agent._refresh_plan_tree(force_reload=True)
            return AgentStep(
                action=action,
                success=success,
                message=outcome.summary,
                details={
                    "plan_id": plan_id,
                    "decomposition_status": readiness.decomposition_status,
                    "auto_generated_changes": True,
                    "optimization_needed": outcome.optimization_needed,
                    "applied_changes": len(outcome.applied_changes),
                    "failed_changes": 0,
                    "generated_changes": list(outcome.generated_changes),
                    "rubric_score_before": (
                        review_before.overall_score if review_before is not None else None
                    ),
                    "rubric_score_after": (
                        review_after.overall_score if review_after is not None else None
                    ),
                    "rubric_score_delta": score_delta,
                    "changes_detail": {
                        "applied": list(outcome.applied_changes),
                        "failed": [],
                    },
                },
            )
        # Use cached review only — do not block structural edits on rubric evaluation.
        # If no cached review exists, score delta will simply be omitted.
        review_before = None
        tree_metadata = getattr(tree, "metadata", None)
        if isinstance(tree_metadata, dict):
            from app.services.plans.plan_optimizer import _coerce_plan_rubric_result
            review_before = _coerce_plan_rubric_result(tree_metadata.get("plan_evaluation"))
        plan_tree_before = tree  # Snapshot before changes are applied
        try:
            applied = repo.apply_changes_atomically(plan_id, changes)
            repo.reindex_all_positions(plan_id)
        except Exception as exc:
            agent._refresh_plan_tree(force_reload=True)
            return AgentStep(
                action=action,
                success=False,
                message=f"Plan #{plan_id} optimization failed: {exc}",
                details={
                    "plan_id": plan_id,
                    "applied_changes": 0,
                    "failed_changes": len(changes),
                    "changes_detail": {
                        "applied": [],
                        "failed": [{"error": str(exc)}],
                    },
                },
            )

        outcome = None
        try:
            outcome = await capture_plan_optimization_outcome(
                plan_id=plan_id,
                plan_tree_before=plan_tree_before,
                applied_changes=applied,
                generated_changes=changes,
                repo=repo,
                summary=f"Applied {len(applied)} explicit plan changes.",
                review_before=review_before,
                auto_generated=False,
                skip_evaluation=review_before is None,
            )
        except Exception as exc:
            logger.warning(
                "capture_plan_optimization_outcome failed (changes already applied): %s",
                exc,
            )
        review_after = (outcome.review_after if outcome is not None else None) or review_before
        score_delta = None
        if review_before is not None and review_after is not None:
            score_delta = float(review_after.overall_score) - float(review_before.overall_score)

        agent._refresh_plan_tree(force_reload=True)
        score_info = ""
        if review_before is not None and review_after is not None and score_delta is not None:
            score_info = f" Rubric {review_before.overall_score:.1f}% -> {review_after.overall_score:.1f}% ({score_delta:+.1f})."
        message = (
            f"Plan #{plan_id} optimized: {len(applied)} changes applied.{score_info}\n"
            "INSTRUCTIONS FOR PRESENTING THE OPTIMIZATION RESULT:\n"
            "1. Show the score change prominently (before → after, delta).\n"
            "2. List each applied change in a table: task ID, change type, what was modified.\n"
            "3. If dimension scores are available, show which dimensions improved or declined.\n"
            "4. Summarize the key improvements in 2-3 sentences.\n"
            "5. If any changes failed, explain why."
        )
        details = {
            "plan_id": plan_id,
            "decomposition_status": readiness.decomposition_status,
            "applied_changes": len(applied),
            "failed_changes": 0,
            "auto_generated_changes": False,
            "generated_changes": list(changes),
            "rubric_score_before": (
                review_before.overall_score if review_before is not None else None
            ),
            "rubric_score_after": (
                review_after.overall_score if review_after is not None else None
            ),
            "rubric_score_delta": score_delta,
            "changes_detail": {"applied": applied, "failed": []},
        }
        return AgentStep(
            action=action,
            success=True,
            message=message,
            details=details,
        )

    return _ah().handle_unknown_action(agent, action)


# ---------------------------------------------------------------------------
# handle_task_action
# ---------------------------------------------------------------------------

def _prepare_rerun_task_execution(
    agent: Any,
    action: LLMAction,
) -> Tuple[PlanTree, int, ExecutionConfig]:
    params = action.parameters or {}
    tree = agent._require_plan_bound()
    task_id_raw = params.get("task_id")
    task_id = agent._coerce_int(task_id_raw, "task_id")
    if agent.plan_executor is None:
        raise ValueError("Plan executor is not enabled in this environment.")

    paper_mode_raw = params.get("paper_mode")
    paper_mode = False
    if isinstance(paper_mode_raw, bool):
        paper_mode = paper_mode_raw
    elif paper_mode_raw is not None:
        paper_mode = str(paper_mode_raw).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
            "y",
        }

    action_metadata = action.metadata if isinstance(action.metadata, dict) else {}
    is_explicit_execute_shortcut = (
        str(action_metadata.get("origin") or "").strip().lower()
        == "explicit_execute_shortcut"
    )

    session_ctx = {
        "session_id": agent.session_id,
        "user_message": (
            agent._current_user_message
            if hasattr(agent, "_current_user_message")
            else None
        ),
        "chat_history": agent.history,
        "chat_history_max_messages": getattr(agent, "max_history_messages", 80),
        "recent_tool_results": agent.extra_context.get("recent_tool_results", []),
        "paper_mode": paper_mode,
        "explicit_execute_shortcut": is_explicit_execute_shortcut,
    }
    exec_config = ExecutionConfig(
        session_context=session_ctx,
        paper_mode=paper_mode,
        enable_skills=not is_explicit_execute_shortcut,
        skill_trace_enabled=not is_explicit_execute_shortcut,
    )
    if is_explicit_execute_shortcut:
        logger.info(
            "[CHAT][EXEC_SHORTCUT] Disabled skill selection for rerun_task task_id=%s",
            task_id,
        )
    return tree, task_id, exec_config


def _ensure_rerun_task_execution_job(
    agent: Any,
    tree: PlanTree,
    task_id: int,
) -> Optional[str]:
    plan_decomposition_jobs = _ah().plan_decomposition_jobs
    extra_context = getattr(agent, "extra_context", None)
    requested_job_id = ""
    if isinstance(extra_context, dict):
        requested_job_id = str(extra_context.get(_RERUN_TASK_EXECUTION_JOB_KEY) or "").strip()

    existing_job = plan_decomposition_jobs.get_job(requested_job_id) if requested_job_id else None
    if existing_job is not None:
        return existing_job.job_id

    task_name = ""
    try:
        task_name = tree.get_node(task_id).display_name()
    except Exception:
        task_name = f"Task {task_id}"

    job_id = requested_job_id or f"plan_execute_{uuid4().hex}"
    try:
        job = plan_decomposition_jobs.create_job(
            plan_id=tree.id,
            task_id=task_id,
            mode="single_task",
            job_type="plan_execute",
            params={
                "session_id": getattr(agent, "session_id", None),
                "task_id": task_id,
                "mode": "rerun_task",
            },
            metadata={
                "session_id": getattr(agent, "session_id", None),
                "conversation_id": getattr(agent, "conversation_id", None),
                "source": "rerun_task",
                "target_task_name": task_name,
            },
            session_id=getattr(agent, "session_id", None),
            job_id=job_id,
        )
        return job.job_id
    except Exception as exc:
        logger.warning("Failed to fully initialize rerun_task job %s: %s", job_id, exc)
        existing_after_failure = plan_decomposition_jobs.get_job(job_id)
        return existing_after_failure.job_id if existing_after_failure is not None else None


def _execute_rerun_task_with_job(
    agent: Any,
    tree: PlanTree,
    task_id: int,
    exec_config: ExecutionConfig,
) -> Tuple[Any, Optional[str]]:
    plan_decomposition_jobs = _ah().plan_decomposition_jobs
    job_id = _ensure_rerun_task_execution_job(agent, tree, task_id)
    job_token = set_current_job(job_id) if job_id else None

    try:
        if job_id:
            try:
                plan_decomposition_jobs.mark_running(job_id)
            except Exception as exc:
                logger.warning("Failed to mark rerun_task job %s running: %s", job_id, exc)
        result = agent.plan_executor.execute_task(tree.id, task_id, config=exec_config)
    except Exception as exc:
        if job_id:
            try:
                plan_decomposition_jobs.mark_failure(
                    job_id,
                    str(exc),
                    result={
                        "plan_id": tree.id,
                        "task_id": task_id,
                        "status": "failed",
                        "content": str(exc),
                    },
                    stats={
                        "plan_id": tree.id,
                        "task_id": task_id,
                        "execution_status": "failed",
                    },
                )
            except Exception as mark_failure_exc:
                logger.warning(
                    "Failed to mark rerun_task job %s failed: %s",
                    job_id,
                    mark_failure_exc,
                )
        raise
    finally:
        if job_token is not None:
            reset_current_job(job_token)

    if job_id:
        result_payload = result.to_dict() if hasattr(result, "to_dict") else None
        status = str(getattr(result, "status", "") or "").strip().lower()
        stats = {
            "plan_id": tree.id,
            "task_id": task_id,
            "execution_status": status or "unknown",
        }
        if status in {"failed", "error"}:
            error_text = str(getattr(result, "content", "") or f"Task {task_id} failed.")
            try:
                plan_decomposition_jobs.mark_failure(
                    job_id,
                    error_text,
                    result=result_payload,
                    stats=stats,
                )
            except Exception as exc:
                logger.warning("Failed to persist rerun_task failure for %s: %s", job_id, exc)
        else:
            try:
                plan_decomposition_jobs.mark_success(
                    job_id,
                    result=result_payload,
                    stats=stats,
                )
            except Exception as exc:
                logger.warning("Failed to persist rerun_task success for %s: %s", job_id, exc)

    return result, job_id


def _finalize_rerun_task_execution(
    agent: Any,
    action: LLMAction,
    tree: PlanTree,
    task_id: int,
    result: Any,
    *,
    job_id: Optional[str] = None,
) -> AgentStep:
    status = str(getattr(result, "status", "") or "").strip().lower()
    success = status in {"completed", "done", "success"}
    message = f"Task [{task_id}] execution status: {getattr(result, 'status', None)}."
    if status == "skipped":
        message = f"Task [{task_id}] was skipped."
    elif status in {"failed", "error"}:
        message = f"Task [{task_id}] failed."
    result_payload = result.to_dict()
    details = dict(result_payload)
    details["result"] = dict(result_payload)
    if job_id:
        details["job"] = {
            "job_id": job_id,
            "job_type": "plan_execute",
            "task_id": task_id,
            "plan_id": tree.id,
        }
    agent._refresh_plan_tree(force_reload=True)
    return AgentStep(
        action=action,
        success=success,
        message=message,
        details=details,
    )


async def handle_task_action_async(agent: Any, action: LLMAction) -> AgentStep:
    if action.name != "rerun_task":
        return _ah().handle_task_action(agent, action)

    tree, task_id, exec_config = _prepare_rerun_task_execution(agent, action)
    result, job_id = await asyncio.to_thread(
        _execute_rerun_task_with_job,
        agent,
        tree,
        task_id,
        exec_config,
    )
    return _finalize_rerun_task_execution(
        agent,
        action,
        tree,
        task_id,
        result,
        job_id=job_id,
    )
