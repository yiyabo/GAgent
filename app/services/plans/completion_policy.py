"""Execution lifecycle is independent of output acceptance.

The former acceptance pipeline remains available for rollback. In the default
mode the task Agent owns its work and input recovery; filesystem publication
and runtime ownership fences still protect real files and concurrent runs.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.foundation.settings import get_settings


def acceptance_enabled() -> bool:
    return bool(getattr(get_settings(), "plan_acceptance_enabled", False))


def agent_managed_node(node) -> bool:
    return bool(getattr(node, "plan_id", 0) > 0 and getattr(node, "id", 0) > 0 and not acceptance_enabled())


def execution_status(raw_status: Any, metadata: dict | None = None) -> str:
    metadata = metadata if isinstance(metadata, dict) else {}
    raw = str(raw_status or "").strip().lower()
    if raw in {"pending", "queued", "running", "delegating", "paused", "cancelled", "canceled"}:
        return raw
    status = str(metadata.get("execution_status") or raw_status or "pending").strip().lower()
    if metadata.get("execution_issues"):
        return "failed"
    if metadata.get("termination_reason") in {"cancelled", "canceled", "run_deadline_exceeded"}:
        return "cancelled" if metadata["termination_reason"] in {"cancelled", "canceled"} else "failed"
    if status in {"completed", "success", "succeeded", "done", "needs_review"}:
        return "completed"
    return "failed" if status in {"failure", "error"} else status


def result_metadata(metadata: dict | None) -> dict:
    result = dict(metadata or {})
    if not acceptance_enabled():
        result["execution_policy"] = "agent"
    else:
        result.pop("execution_policy", None)
    return result


def code_spec(spec):
    if not isinstance(spec, dict) or not spec.get("plan_id") or not spec.get("task_id") or acceptance_enabled():
        return spec
    return {**spec, "agent_managed_completion": True, "acceptance_criteria": None,
            "dependency_warnings": spec.get("dependency_blockers") or [], "dependency_blockers": []}


def finalize(service, node, payload, raw_status):
    """Collect real output references without running or interpreting check rules."""
    from .task_verification import VerificationFinalization

    value = deepcopy(service._coerce_payload(payload, fallback_status=raw_status))
    metadata = value.setdefault("metadata", {})
    status = execution_status(raw_status, {**metadata, "execution_status": raw_status})
    metadata.update(execution_status=status, execution_policy="agent", verification_status="disabled",
                    verification_authoritative=False)
    if metadata.get("verification"):
        metadata["previous_verification"] = metadata["verification"]
    metadata["verification"] = {"status": "disabled", "blocking": False, "authoritative": False}
    if status == "completed":
        for key in ("failure_kind", "contract_diff", "plan_patch_suggestion", "delivery_verification"):
            metadata.pop(key, None)
    paths = service._normalize_artifact_paths(service._extract_artifact_paths(value), payload=value)
    base = service._resolve_base_dir(None, paths, payload=value, node=node)
    paths = service._augment_artifact_paths_with_discovered_outputs(
        node=node, criteria=None, payload=value, artifact_paths=paths, base_dir=base)
    value.update(status=status, metadata=metadata, artifact_paths=paths)
    metadata["artifact_paths"] = list(paths)
    return VerificationFinalization(final_status=status, execution_status=status, payload=value,
                                    artifact_paths=paths, verification=metadata["verification"])


def guidance() -> str:
    return (
        "Execution completion records that this task ran; it is not an independent quality approval. "
        "Use your judgement to finish the requested work and save useful outputs, then submit the result. "
        "Inspect and reuse upstream files by content, even when names or aliases differ or no publication exists. "
        "If an input is missing or incomplete, recover it from existing materials or produce the necessary supplement "
        "within this task before continuing. Preserve provenance and report unresolved gaps honestly; never invent evidence. "
        "This permission covers supporting work and a private todo list, not editing, splitting or rerunning the persisted "
        "Plan tree. Only the user can authorize changes to that tree."
    )


def plan_agent_managed(agent, context=None, task_context=None) -> bool:
    if acceptance_enabled():
        return False
    context = context or {}
    if context.get("plan_id") and (context.get("task_id") or getattr(task_context, "task_id", None)):
        return True
    profile = getattr(agent, "request_profile", {}) or {}
    return bool((profile.get("plan_id") or profile.get("current_plan_id")) and profile.get("current_task_id")
                and profile.get("intent_type") == "execute_task")
