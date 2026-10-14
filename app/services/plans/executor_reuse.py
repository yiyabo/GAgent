"""Reuse settled upstream tasks only after current status and output checks."""
from copy import deepcopy
import json
from .executor_models import ExecutionResult
from app.services.run_budget import check_run_active


def completed_result(executor, plan_id, task_id, session_context):
    check_run_active()
    tree = executor._repo.get_plan_tree(plan_id)
    node = tree.nodes.get(task_id)
    if node is None or node.status != "completed" or not node.execution_result:
        return None
    manifest = executor._get_artifact_manifest(plan_id, session_context)
    states = executor._status_resolver.resolve_plan_states(plan_id, tree, manifest=manifest)
    if states.get(task_id, {}).get("effective_status") != "completed":
        return None
    try:
        payload = json.loads(node.execution_result)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    finalized, _ = executor._finalize_task_execution(plan_id, node, deepcopy(payload),
        execution_status="completed", trigger="reuse")
    if finalized.final_status != "completed":
        return None
    metadata = {**(finalized.payload.get("metadata") or {}), "reused": True}
    return ExecutionResult(plan_id=plan_id, task_id=task_id, status="completed",
        content=str(finalized.payload.get("content") or ""), metadata=metadata,
        raw_response=node.execution_result, attempts=0, duration_sec=0)
