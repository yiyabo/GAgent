"""Resume a previously verified delivery gap using existing local evidence."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Iterable, Optional

from .plan_models import PlanNode
from .task_verification import VerificationFinalization
from .executor_history import task_binding


def _output_targets(spec: object) -> object:
    if not isinstance(spec, dict) or not isinstance(spec.get("required_outputs"), list):
        return None
    if not spec["required_outputs"] or any(not isinstance(item, dict) for item in spec["required_outputs"]):
        return None
    return [
        (str(item.get("kind") or ""), str(item.get("target_path") or ""),
         str(item.get("min_count") or 1), tuple(item.get("extensions") or []),
         str(item.get("constraints") or ""))
        for item in spec["required_outputs"] if isinstance(item, dict)
    ]


def previous_delivery_gap(node: PlanNode) -> Optional[VerificationFinalization]:
    """Reuse a prior contract verdict; the current verifier still decides success."""
    if node.status != "failed" or not node.execution_result:
        return None
    try:
        prior = json.loads(node.execution_result)
    except (TypeError, ValueError):
        return None
    if not isinstance(prior, dict) or prior.get("status") != "failed":
        return None
    metadata = prior.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("failure_kind") != "contract_mismatch":
        return None
    if metadata.get("blocked_by_dependencies") or metadata.get("execution_issues"):
        return None
    if metadata.get("termination_reason") in {"cancelled", "run_deadline_exceeded"}:
        return None
    history_ref = metadata.get("execution_history_ref") or {}
    if history_ref and history_ref.get("scope", {}).get("goal_binding") != task_binding(node):
        return None

    diff = metadata.get("contract_diff")
    if not isinstance(diff, dict):
        return None
    current_spec = (node.metadata or {}).get("output_spec")
    prior_spec = metadata.get("output_spec")
    if prior_spec is not None and current_spec is not None:
        prior_targets, current_targets = _output_targets(prior_spec), _output_targets(current_spec)
        if prior_targets is None or current_targets is None or prior_targets != current_targets:
            return None

    verification = metadata.get("verification")
    output_check = metadata.get("output_verification")
    if not any(isinstance(record, dict) and record.get("status") == "failed"
               for record in (verification, output_check)):
        return None
    if not any(isinstance(record, dict) and record.get("failures")
               for record in (verification, output_check)):
        return None

    retained = {key: deepcopy(metadata[key]) for key in (
        "failure_kind", "contract_diff", "verification", "output_verification", "tool_failures",
        "execution_history_ref",
    ) if key in metadata}
    retained["resumed_delivery_gap"] = True
    raw_paths = prior.get("artifact_paths")
    paths = [str(item) for item in raw_paths if isinstance(item, str)] if isinstance(raw_paths, list) else []
    payload = {
        "status": "failed",
        "content": str(prior.get("content") or "The previous attempt left verified delivery requirements unmet."),
        "artifact_paths": paths,
        "metadata": retained,
    }
    return VerificationFinalization(
        final_status="failed", execution_status="failed", payload=payload,
        verification=deepcopy(verification) if isinstance(verification, dict) else None,
        artifact_paths=paths,
    )


def has_existing_evidence(
    prior: VerificationFinalization,
    dependency_paths: Iterable[str],
    task_work_dir: str,
) -> bool:
    """Use only durable dependency files or files already in this task workspace."""
    def nonempty_file(raw: str) -> bool:
        try:
            path = Path(raw)
            return path.is_file() and path.stat().st_size > 0
        except (OSError, ValueError):
            return False

    if any(nonempty_file(path) for path in dependency_paths if isinstance(path, str)):
        return True
    try:
        work_dir = Path(task_work_dir).resolve()
    except (OSError, ValueError):
        return False
    for raw in prior.artifact_paths:
        try:
            path = Path(raw).resolve()
            if nonempty_file(str(path)) and work_dir in path.parents:
                return True
        except (OSError, ValueError):
            continue
    return False
