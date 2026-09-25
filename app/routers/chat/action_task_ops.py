"""Task-operation dispatch cluster of ``action_handlers``.

Moved out of ``action_handlers.py`` per
design/2026-09-24-backend-godfiles-refactor-plan.md §4.7 (handlers cluster ④'s
``action_task_ops``): ``handle_task_action`` with its ten sub-branches
(create/update/update_instruction/move/delete/show/query_status/rerun/verify/
decompose).

Sanctioned deviations (three lines total):
- ``_task_verifier`` and ``_normalize_dependencies_fn`` are facade-level
  bindings, so they are read through ``_ah()`` once per call (two alias lines at
  the top of the function) instead of being imported by value — a patch on the
  action_handlers namespace still wins, and every call expression stays
  verbatim.  ``_task_verifier`` is the verification service singleton the
  ``verify_task`` branch drives (including `_has_checks` / `_is_local_path` /
  `_build_generated_criteria`, which stay defined on its class);
  ``_normalize_dependencies_fn`` is the facade's alias of
  ``tool_results.normalize_dependencies``.
- the fallback ``return handle_unknown_action(agent, action)`` reaches the
  facade helper through ``_ah()``.

The module uses its own ``logging.getLogger(__name__)`` (split precedent); every
log message, AgentStep payload and error string is unchanged.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from app.services.llm.structured_response import LLMAction

from .action_plan_ops import (
    _execute_rerun_task_with_job,
    _finalize_rerun_task_execution,
    _prepare_rerun_task_execution,
)
from .models import AgentStep

logger = logging.getLogger(__name__)


def _ah() -> Any:
    """Late-bound action_handlers facade module (monkeypatch-friendly lookups)."""
    from . import action_handlers

    return action_handlers


def handle_task_action(agent: Any, action: LLMAction) -> AgentStep:
    _task_verifier = _ah()._task_verifier
    _normalize_dependencies_fn = _ah()._normalize_dependencies_fn
    params = action.parameters or {}
    tree = agent._require_plan_bound()

    if action.name == "create_task":
        name = params.get("task_name") or params.get("name") or params.get("title")
        if not name:
            raise ValueError("create_task requires a task_name.")
        instruction = params.get("instruction")
        parent_id = params.get("parent_id")
        if parent_id is not None:
            parent_id = agent._coerce_int(parent_id, "parent_id")
        metadata = (
            params.get("metadata")
            if isinstance(params.get("metadata"), dict)
            else None
        )
        dependencies = _normalize_dependencies_fn(params.get("dependencies"))

        raw_anchor_task_id = params.get("anchor_task_id")
        anchor_task_id = None
        if raw_anchor_task_id is not None:
            anchor_task_id = agent._coerce_int(raw_anchor_task_id, "anchor_task_id")

        anchor_position = params.get("anchor_position")
        if anchor_position is not None and not isinstance(anchor_position, str):
            raise ValueError("anchor_position must be a string.")
        if isinstance(anchor_position, str):
            anchor_position = anchor_position.strip()
            anchor_position = anchor_position.lower() if anchor_position else None

        position_param = params.get("position")
        position: Optional[int] = None
        if position_param is not None:
            if isinstance(position_param, str):
                position_str = position_param.strip()
                if position_str:
                    parts = position_str.split(":", 1)
                    keyword = parts[0].strip().lower()
                    if keyword in {"before", "after"}:
                        if len(parts) < 2 or not parts[1].strip():
                            # Support shorthand "before"/"after":
                            # - If anchor_task_id is provided separately, treat as relative to it.
                            # - Otherwise, map to inserting as first/last child.
                            derived_position = keyword
                            if anchor_task_id is None:
                                derived_position = (
                                    "first_child" if keyword == "before" else "last_child"
                                )
                            if anchor_position is not None and anchor_position != derived_position:
                                raise ValueError(
                                    "anchor_position does not match the pattern specified in position."
                                )
                            anchor_position = derived_position
                        else:
                            candidate_id = agent._coerce_int(parts[1].strip(), f"position {keyword}")
                            if anchor_task_id is not None and anchor_task_id != candidate_id:
                                raise ValueError(
                                    "anchor_task_id does not match the task referenced in position."
                                )
                            if anchor_position is not None and anchor_position != keyword:
                                raise ValueError(
                                    "anchor_position does not match the pattern specified in position."
                                )
                            anchor_task_id = candidate_id
                            anchor_position = keyword
                    elif keyword in {"first_child", "last_child"}:
                        if anchor_position is not None and anchor_position != keyword:
                            raise ValueError(
                                "anchor_position does not match the pattern specified in position."
                            )
                        anchor_position = keyword
                    else:
                        position = agent._coerce_int(position_param, "position")
                else:
                    position = None
            else:
                position = agent._coerce_int(position_param, "position")

        if position is not None and position < 0:
            raise ValueError("position cannot be negative.")

        insert_before_val = params.get("insert_before")
        insert_after_val = params.get("insert_after")
        insert_before_id = (
            agent._coerce_int(insert_before_val, "insert_before")
            if insert_before_val is not None
            else None
        )
        insert_after_id = (
            agent._coerce_int(insert_after_val, "insert_after")
            if insert_after_val is not None
            else None
        )

        siblings_parent_key = parent_id if parent_id is not None else None
        siblings = tree.children_ids(siblings_parent_key)

        if insert_before_id is not None and insert_after_id is not None:
            if insert_before_id == insert_after_id:
                raise ValueError("insert_before and insert_after cannot point to the same task.")
            if insert_after_id not in siblings or insert_before_id not in siblings:
                raise ValueError("insert_before / The task referenced by insert_after does not belong to the target parent node.")
            after_idx = siblings.index(insert_after_id)
            before_idx = siblings.index(insert_before_id)
            if after_idx > before_idx:
                raise ValueError("insert_after must appear before insert_before.")
            if anchor_task_id is not None and anchor_task_id not in {
                insert_after_id,
                insert_before_id,
            }:
                raise ValueError("anchor_task_id is inconsistent with insert_before/insert_after.")
            anchor_task_id = insert_after_id
            anchor_position = "after"
        else:
            if insert_before_id is not None:
                if anchor_task_id is not None and anchor_task_id != insert_before_id:
                    raise ValueError("anchor_task_id points to a different task than insert_before.")
                if insert_before_id not in siblings:
                    raise ValueError("The task referenced by insert_before does not belong to the target parent node.")
                anchor_task_id = insert_before_id
                anchor_position = "before"
            if insert_after_id is not None:
                if anchor_task_id is not None and anchor_task_id != insert_after_id:
                    raise ValueError("anchor_task_id points to a different task than insert_after.")
                if insert_after_id not in siblings:
                    raise ValueError("The task referenced by insert_after does not belong to the target parent node.")
                anchor_task_id = insert_after_id
                anchor_position = "after"
        if anchor_position is not None:
            valid_anchor_positions = {
                "before",
                "after",
                "first_child",
                "last_child",
            }
            if anchor_position not in valid_anchor_positions:
                raise ValueError(
                    f"Invalid anchor_position; only {', '.join(sorted(valid_anchor_positions))} are supported."
                )
        node = agent.plan_session.repo.create_task(
            tree.id,
            name=name,
            instruction=instruction,
            parent_id=parent_id,
            metadata=metadata,
            dependencies=dependencies,
            position=position,
            anchor_task_id=anchor_task_id,
            anchor_position=anchor_position,
        )
        agent._refresh_plan_tree()
        message = f"Created task [{node.id}] {node.name}."
        details = {"task": node.model_dump()}
        agent._dirty = True
        return AgentStep(
            action=action, success=True, message=message, details=details
        )

    if action.name == "update_task":
        task_id = agent._coerce_int(params.get("task_id"), "task_id")
        name = params.get("name")
        instruction = params.get("instruction")
        metadata = (
            params.get("metadata")
            if isinstance(params.get("metadata"), dict)
            else None
        )
        dependencies = _normalize_dependencies_fn(params.get("dependencies"))
        node = agent.plan_session.repo.update_task(
            tree.id,
            task_id,
            name=name,
            instruction=instruction,
            metadata=metadata,
            dependencies=dependencies,
        )
        agent._refresh_plan_tree()
        message = f"Task [{node.id}] information has been updated."
        details = {"task": node.model_dump()}
        agent._dirty = True
        return AgentStep(
            action=action, success=True, message=message, details=details
        )

    if action.name == "update_task_instruction":
        task_id = agent._coerce_int(params.get("task_id"), "task_id")
        instruction = params.get("instruction")
        if not instruction:
            raise ValueError("update_task_instruction requires an instruction.")
        node = agent.plan_session.repo.update_task(
            tree.id,
            task_id,
            instruction=instruction,
        )
        agent._refresh_plan_tree()
        message = f"Task [{node.id}] instructions have been updated."
        details = {"task": node.model_dump()}
        agent._dirty = True
        return AgentStep(
            action=action, success=True, message=message, details=details
        )

    if action.name == "move_task":
        task_id = agent._coerce_int(params.get("task_id"), "task_id")
        new_parent_id = params.get("new_parent_id")
        if new_parent_id is not None:
            new_parent_id = agent._coerce_int(new_parent_id, "new_parent_id")
        new_position = params.get("new_position")
        if new_position is not None:
            new_position = agent._coerce_int(new_position, "new_position")
        node = agent.plan_session.repo.move_task(
            tree.id,
            task_id,
            new_parent_id=new_parent_id,
            new_position=new_position,
        )
        agent._refresh_plan_tree()
        message = f"Task [{node.id}] has been moved to a new position."
        details = {"task": node.model_dump()}
        agent._dirty = True
        return AgentStep(
            action=action, success=True, message=message, details=details
        )

    if action.name == "delete_task":
        task_id = agent._coerce_int(params.get("task_id"), "task_id")
        agent.plan_session.repo.delete_task(tree.id, task_id)
        agent._refresh_plan_tree()
        message = f"Task [{task_id}] and its subtasks have been deleted."
        details = {"task_id": task_id}
        agent._dirty = True
        return AgentStep(
            action=action, success=True, message=message, details=details
        )

    if action.name == "show_tasks":
        agent._refresh_plan_tree(force_reload=False)
        outline = agent.plan_session.outline(max_depth=6, max_nodes=120)
        message = f"Here is the task overview for plan #{tree.id}."
        details = {"plan_id": tree.id, "outline": outline}
        return AgentStep(
            action=action, success=True, message=message, details=details
        )

    if action.name == "query_status":
        agent._refresh_plan_tree(force_reload=False)
        node_count = agent.plan_tree.node_count() if agent.plan_tree else 0
        root_count = len(agent.plan_tree.root_node_ids()) if agent.plan_tree else 0
        message = f"Plan #{tree.id} currently has {node_count} task nodes ({root_count} roots)."
        details = {
            "plan_id": tree.id,
            "task_count": node_count,
            "root_tasks": root_count,
        }
        return AgentStep(
            action=action, success=True, message=message, details=details
        )

    if action.name == "rerun_task":
        tree, task_id, exec_config = _prepare_rerun_task_execution(agent, action)
        result, job_id = _execute_rerun_task_with_job(agent, tree, task_id, exec_config)
        return _finalize_rerun_task_execution(
            agent,
            action,
            tree,
            task_id,
            result,
            job_id=job_id,
        )

    if action.name == "verify_task":
        task_id = agent._coerce_int(params.get("task_id"), "task_id")
        if not tree.has_node(task_id):
            raise ValueError(f"Task {task_id} not found in plan {tree.id}")
        node = tree.get_node(task_id)
        if not node.execution_result:
            child_ids = list(tree.children_ids(task_id)) if hasattr(tree, "children_ids") else []
            leaf_child_ids = [
                child_id
                for child_id in child_ids
                if not tree.children_ids(child_id)
            ] if child_ids and hasattr(tree, "children_ids") else []
            if child_ids:
                message = (
                    f"Task [{task_id}] is a composite parent and has no direct execution result to verify; "
                    "verify one of its executable child tasks instead."
                )
            else:
                message = f"Task [{task_id}] has not produced an execution result yet; run it before verification."
            return AgentStep(
                action=action,
                success=False,
                message=message,
                details={
                    "task_id": task_id,
                    "plan_id": tree.id,
                    "child_task_ids": child_ids,
                    "verifiable_task_ids": leaf_child_ids,
                    "verification_status": "not_run",
                },
            )

        # Collect override criteria from multiple sources the LLM might use:
        #   1. params.verification_criteria  – shorthand strings (preferred)
        #   2. params.acceptance_criteria     – full dict
        #   3. action.metadata.acceptance_criteria – LLM sometimes puts hints there
        #   4. action.metadata.verification_criteria – shorthand in metadata
        override_criteria = None
        raw_vc = params.get("verification_criteria")
        action_meta = action.metadata if isinstance(action.metadata, dict) else {}

        if not isinstance(raw_vc, list) or not raw_vc:
            raw_vc = action_meta.get("verification_criteria")
        if not isinstance(raw_vc, list) or not raw_vc:
            raw_vc = params.get("acceptance_criteria")
        if not isinstance(raw_vc, list) or not raw_vc:
            raw_vc = action_meta.get("acceptance_criteria")

        if isinstance(raw_vc, list) and raw_vc:
            # Check if items are shorthand strings or full dict checks
            if all(isinstance(item, str) for item in raw_vc):
                override_criteria = _task_verifier.parse_shorthand_criteria(raw_vc)
            elif all(isinstance(item, dict) for item in raw_vc):
                override_criteria = {
                    "category": "file_data",
                    "blocking": True,
                    "checks": list(raw_vc),
                }

        # Also accept a pre-formed dict in params.acceptance_criteria
        if not override_criteria:
            raw_ac = params.get("acceptance_criteria")
            if not isinstance(raw_ac, dict):
                raw_ac = action_meta.get("acceptance_criteria")
            if isinstance(raw_ac, dict) and raw_ac.get("checks"):
                override_criteria = raw_ac

        # Last resort: if the node has no acceptance_criteria and we have no
        # override, try to build basic checks from the task's execution_result
        # artifact paths so we don't always skip.
        if not override_criteria:
            existing_criteria = (
                node.metadata.get("acceptance_criteria")
                if isinstance(node.metadata, dict) else None
            )
            if not _task_verifier._has_checks(existing_criteria):
                try:
                    raw_payload = json.loads(node.execution_result) if isinstance(node.execution_result, str) else {}
                    artifact_paths = _task_verifier.collect_artifact_paths(raw_payload)
                    local_paths = [p for p in artifact_paths if _task_verifier._is_local_path(p)]
                    if local_paths:
                        override_criteria = _task_verifier._build_generated_criteria(local_paths)
                        generated_checks = (
                            override_criteria.get("checks", [])
                            if isinstance(override_criteria, dict)
                            else []
                        )
                        logger.info(
                            "verify_task: auto-generated %d checks from artifact paths for task %s",
                            len(generated_checks),
                            task_id,
                        )
                except Exception:
                    pass

        if override_criteria and _task_verifier._has_checks(override_criteria):
            raw_override_checks = (
                override_criteria.get("checks", [])
                if isinstance(override_criteria, dict)
                else []
            )
            override_checks = raw_override_checks if isinstance(raw_override_checks, list) else []
            logger.info(
                "verify_task: using %d override checks for task %s",
                len(override_checks),
                task_id,
            )

        try:
            finalization = _task_verifier.verify_task(
                agent.plan_session.repo,
                plan_id=tree.id,
                task_id=task_id,
                trigger="manual",
                override_criteria=override_criteria,
            )
        except Exception as verify_err:
            logger.warning("verify_task failed for task %s: %s", task_id, verify_err)
            return AgentStep(
                action=action,
                success=False,
                message=f"Task [{task_id}] verification error: {verify_err}",
                details={"task_id": task_id, "plan_id": tree.id},
            )
        verification = finalization.verification or {}
        verification_status = str(verification.get("status") or "skipped")
        checks_total = int(verification.get("checks_total", 0) or 0)
        checks_passed = int(verification.get("checks_passed", 0) or 0)
        needs_criteria = bool(verification.get("needs_criteria"))
        if verification_status == "passed":
            message = (
                f"Task [{task_id}] verification passed "
                f"({checks_passed}/{checks_total} checks)."
            )
        elif verification_status == "failed":
            message = (
                f"Task [{task_id}] verification failed "
                f"({checks_passed}/{checks_total} checks passed)."
            )
        elif needs_criteria:
            message = (
                f"Task [{task_id}] verification skipped: no acceptance_criteria or "
                f"verification_criteria provided. You MUST pass verification_criteria "
                f"with concrete check strings (e.g. 'file_exists:/path/to/output.csv') "
                f"for the verifier to run actual checks."
            )
        else:
            message = f"Task [{task_id}] verification skipped."
        verification_success = verification_status == "passed"
        if verification_status == "skipped" and not needs_criteria:
            verification_success = True
        agent._refresh_plan_tree(force_reload=True)
        return AgentStep(
            action=action,
            success=verification_success,
            message=message,
            details={
                "task_id": task_id,
                "plan_id": tree.id,
                "status": finalization.final_status,
                "verification": verification,
                "payload": finalization.payload,
                # "result" key required by _normalize_deep_think_tool_result so
                # the DeepThink tool-wrapper uses the correct success flag.
                "result": {
                    "success": verification_success,
                    "task_id": task_id,
                    "plan_id": tree.id,
                    "verification_status": verification_status,
                    "checks_passed": checks_passed,
                    "checks_total": checks_total,
                    "final_status": finalization.final_status,
                    "summary": message,
                },
            },
        )

    if action.name == "decompose_task":
        if agent.plan_decomposer is None:
            raise ValueError("Task decomposition service is not enabled in this environment.")
        if agent.decomposer_settings.model is None:
            raise ValueError("No decomposition model configured; cannot proceed.")

        expand_depth_raw = params.get("expand_depth")
        node_budget_raw = params.get("node_budget")
        allow_existing_raw = params.get("allow_existing_children")

        expand_depth = (
            agent._coerce_int(expand_depth_raw, "expand_depth")
            if expand_depth_raw is not None
            else None
        )
        node_budget = (
            agent._coerce_int(node_budget_raw, "node_budget")
            if node_budget_raw is not None
            else None
        )
        allow_existing_children = None
        if allow_existing_raw is not None:
            if isinstance(allow_existing_raw, bool):
                allow_existing_children = allow_existing_raw
            else:
                allow_existing_children = str(
                    allow_existing_raw
                ).strip().lower() in {
                    "1",
                    "true",
                    "yes",
                    "y",
                }

        task_id_raw = params.get("task_id")
        # Build session context and pass to plan decomposer.
        session_ctx = {
            "user_message": agent._current_user_message if hasattr(agent, "_current_user_message") else None,
            "chat_history": agent.history,
            "chat_history_max_messages": getattr(agent, "max_history_messages", 80),
            "recent_tool_results": agent.extra_context.get("recent_tool_results", []),
        }
        if task_id_raw is None:
            result = agent.plan_decomposer.run_plan(
                tree.id,
                max_depth=expand_depth,
                node_budget=node_budget,
                session_context=session_ctx,
            )
        else:
            task_id = agent._coerce_int(task_id_raw, "task_id")
            result = agent.plan_decomposer.decompose_node(
                tree.id,
                task_id,
                expand_depth=expand_depth,
                node_budget=node_budget,
                allow_existing_children=allow_existing_children,
                session_context=session_ctx,
            )

        agent._last_decomposition = result
        if result.created_tasks:
            agent._dirty = True
        try:
            agent._refresh_plan_tree(force_reload=True)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Failed to refresh plan tree after decomposition: %s", exc
            )
            agent._decomposition_errors.append(f"Failed to refresh plan after decomposition: {exc}")

        created_count = len(result.created_tasks)
        message = (
            f"Generated {created_count} subtasks."
            if created_count
            else "No new subtasks were generated."
        )
        if result.stopped_reason:
            message += f" Stop reason: {result.stopped_reason}."
        details = {
            "plan_id": tree.id,
            "mode": result.mode,
            "processed_nodes": result.processed_nodes,
            "created": [node.model_dump() for node in result.created_tasks],
            "failed_nodes": result.failed_nodes,
            "stopped_reason": result.stopped_reason,
            "stats": result.stats,
        }
        return AgentStep(
            action=action, success=True, message=message, details=details
        )

    return _ah().handle_unknown_action(agent, action)
