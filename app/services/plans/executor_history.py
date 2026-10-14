"""Small references to task-local transcripts; no transcript duplication in API payloads."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from app.services.deep_think.checkpointing import ControllerRestoreError
from app.services.run_budget import check_run_active


def task_binding(node):
    metadata = node.metadata or {}
    contract = {k: deepcopy(metadata.get(k)) for k in (
        "output_spec", "required_outputs", "acceptance_criteria", "artifact_contract")}
    # The existing executor normalizes absent aliases to empty lists. That is
    # not a user edit and must not invalidate an otherwise identical transcript.
    for container in (contract, contract.get("output_spec")):
        if isinstance(container, dict):
            aliases = container.get("artifact_contract") or {}
            container["artifact_contract"] = {**aliases,
                "requires": sorted(aliases.get("requires") or []),
                "publishes": sorted(aliases.get("publishes") or [])}
    return _digest({"name": node.name, "instruction": node.instruction,
                    "contract": contract})


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_encode(value)).hexdigest()


def history_scope(node, context, dependency_outputs):
    return {"plan_id": node.plan_id, "task_id": node.id,
            "session_id": context.get("session_id"), "goal_binding": task_binding(node),
            "inputs": _digest({"snapshot": context.get("output_input_snapshot"),
                               "dependencies": dependency_outputs})}


def save_history(base_dir, continuation):
    if not continuation or not base_dir:
        return None
    check_run_active()
    root = Path(base_dir).resolve()
    directory = root / "scratch" / ".execution"
    directory.mkdir(parents=True, exist_ok=True)
    if not directory.resolve().is_relative_to(root):
        raise ControllerRestoreError("Task history directory escaped its workspace")
    target = directory / (uuid4().hex + ".json")
    temporary = target.with_suffix(".tmp")
    data = _encode(continuation)
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        check_run_active()
        os.replace(temporary, target)
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)
    return {"version": 1, "path": str(target), "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "scope": deepcopy(continuation["scope"])}


def load_history(reference, base_dir, scope):
    if not reference:
        return None
    if not isinstance(reference, dict) or reference.get("version") != 1:
        raise ControllerRestoreError("Invalid task history reference")
    # A new goal/input binding starts fresh, rather than recalling obsolete work.
    if reference.get("scope") != scope:
        return None
    check_run_active()
    try:
        root = Path(base_dir).resolve()
        path = Path(reference["path"]).resolve()
        directory = (root / "scratch" / ".execution").resolve()
        if not directory.is_relative_to(root) or path.parent != directory:
            raise ValueError("History reference is outside the task directory")
        if path.stat().st_size != reference.get("bytes"):
            raise ValueError("History size changed")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != reference.get("sha256"):
            raise ValueError("History checksum changed")
        continuation = json.loads(data)
        if continuation.get("scope") != scope:
            raise ValueError("History task binding changed")
        return continuation
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ControllerRestoreError("Task execution history could not be restored") from exc
