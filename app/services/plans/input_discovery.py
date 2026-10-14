"""Let the task's existing tool loop inspect and bind real upstream materials.

Aliases are lookup hints. A semantic choice is recorded separately from the
persistent task tree and rechecked against the source bytes on every reuse.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .artifact_contracts import resolve_manifest_aliases
from .artifact_versions import definition


def enabled() -> bool:
    from app.services.foundation.settings import get_settings

    return getattr(get_settings(), "plan_input_discovery_enabled", True)


def _payload(node) -> dict:
    try:
        raw = node.execution_result
        value = json.loads(raw) if isinstance(raw, str) else raw
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError, AttributeError):
        return {}


def _sha(path: Path) -> str:
    if path.is_dir():
        from .artifact_versions import content_hash

        return content_hash(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def existing_bindings(node, manifest: dict, metadata: dict | None = None) -> dict:
    """A recorded choice is usable only for this goal and unchanged real bytes."""
    metadata = metadata if isinstance(metadata, dict) else _payload(node).get("metadata", {})
    records = metadata.get("input_artifact_bindings") if isinstance(metadata, dict) else None
    if not isinstance(records, dict):
        return {}
    valid = {}
    for alias, record in records.items():
        if not isinstance(record, dict) or record.get("definition") != definition(node):
            continue
        if record.get("plan_id") != node.plan_id or record.get("task_id") != node.id:
            continue
        path = Path(str(record.get("path") or ""))
        source = Path(str(record.get("source_path") or path))
        try:
            if not path.exists() or _sha(path) != record.get("sha256"):
                continue
            if not source.exists() or _sha(source) != record.get("sha256"):
                continue
            source_alias = record.get("source_alias")
            if source_alias:
                current = (manifest.get("artifacts") or {}).get(source_alias) or {}
                if current.get("artifact_version_id") != record.get("source_version_id"):
                    continue
                resolved = resolve_manifest_aliases(manifest, [source_alias])
                if source_alias not in resolved or Path(resolved[source_alias]).resolve() != source.resolve():
                    continue
        except (OSError, ValueError):
            continue
        valid[alias] = record
    return valid


def candidates(tree, node, manifest: dict) -> list[dict]:
    """Inventory verified outputs across this Plan; never choose by alias similarity."""
    found: dict[str, dict] = {}
    artifacts = manifest.get("artifacts") or {}
    resolved = resolve_manifest_aliases(manifest, artifacts)
    for alias, path in resolved.items():
        entry = artifacts.get(alias) or {}
        producer = tree.nodes.get(entry.get("producer_task_id"))
        if producer is None or producer.id == node.id or producer.status not in {"completed", "done", "success"}:
            continue
        found[str(Path(path).resolve())] = {
            "source_alias": alias,
            "source_version_id": entry.get("artifact_version_id"),
            "producer_task_id": producer.id,
            "producer_name": producer.display_name(),
        }
    # Verified declared files can exist before canonical publication in older plans.
    from .output_spec import output_spec_from_metadata

    for producer in tree.nodes.values():
        if producer.id == node.id or producer.status not in {"completed", "done", "success"}:
            continue
        payload = _payload(producer)
        if (payload.get("metadata") or {}).get("verification_status") != "passed":
            continue
        spec = output_spec_from_metadata(producer.metadata or {})
        declared = (
            {Path(item.target_path).name for item in spec.required_outputs if item.target_path} if spec else set()
        )
        for raw in payload.get("artifact_paths") or []:
            path = Path(str(raw))
            if not path.is_absolute() or path.name not in declared:
                continue
            found.setdefault(
                str(path.resolve()),
                {
                    "source_alias": None,
                    "source_version_id": None,
                    "producer_task_id": producer.id,
                    "producer_name": producer.display_name(),
                },
            )
    result = []
    for raw, record in found.items():
        path = Path(raw)
        try:
            if not path.exists() or path.is_file() and path.stat().st_size <= 0:
                continue
            if path.is_dir() and not any(path.iterdir()):
                continue
            result.append(
                {
                    **record,
                    "candidate_id": f"input-{len(result)+1}",
                    "path": raw,
                    "kind": "directory" if path.is_dir() else "file",
                    "bytes": path.stat().st_size,
                    "sha256": _sha(path),
                }
            )
        except OSError:
            continue
        if len(result) == 40:
            break
    return result


def prepare(node, tree, manifest, missing, context) -> bool:
    items = candidates(tree, node, manifest)
    if not items:
        return False
    context["artifact_input_discovery"] = {
        "missing": list(missing),
        "candidates": items,
        "read_paths": [],
        "bindings": {},
    }
    from .artifact_contracts import artifact_manifest_path

    context["artifact_input_discovery"]["manifest_on_disk"] = artifact_manifest_path(
        node.plan_id, context.get("session_id")
    ).exists()
    return True


def prompt(context) -> str:
    state = context.get("artifact_input_discovery")
    if not state:
        return ""
    return (
        "INPUT RECONCILIATION: The required input aliases were not resolved; this does not establish that files are absent. "
        "Inspect the actual candidate contents using file_operations read or document_reader. Decide whether a candidate "
        "satisfies the input needed by the original task, using its content and provenance, not just its name. "
        "For each confirmed input call plan_operation with operation=resolve_input, input_alias, candidate_id and reason. "
        "This records an input binding for this execution; it does not edit the task tree or mark an output complete. "
        "Resolve all missing inputs before searches, computation or writing task outputs. If no material satisfies the "
        "requirement, report the exact unresolved input; do not recreate upstream work or invent evidence.\n"
        + json.dumps({"missing": state["missing"], "candidates": state["candidates"]}, ensure_ascii=False)
    )


def pending(context) -> list[str]:
    state = context.get("artifact_input_discovery") or {}
    return [alias for alias in state.get("missing", []) if alias not in state.get("bindings", {})]


def guard(context, tool, params) -> dict | None:
    missing = pending(context)
    if not missing:
        return None
    allowed = (
        tool in {"document_reader", "vision_reader"}
        or tool == "file_operations"
        and params.get("operation") in {"read", "list", "info", "stat", "search"}
        or tool == "plan_operation"
        and params.get("operation") == "resolve_input"
    )
    if allowed:
        return None
    return {
        "success": False,
        "executed": False,
        "error_code": "input_confirmation_required",
        "missing_inputs": missing,
        "error": "Read the supplied candidate files and resolve_input before executing the task.",
    }


def note_read(context, tool, params, result):
    state = context.get("artifact_input_discovery")
    if not state or not result.get("success"):
        return
    if tool == "file_operations" and params.get("operation") == "read" or tool == "document_reader":
        raw = params.get("path") or params.get("file_path")
        if raw:
            state["read_paths"].append(str(Path(raw).resolve()))


def bind(context, node, manifest, params):
    from app.services.run_budget import check_run_active

    check_run_active()
    if params.get("plan_id") not in (None, node.plan_id) or params.get("target_task_id") not in (None, node.id):
        return {"success": False, "executed": False, "error": "Input confirmation belongs to the current task only."}
    state = context.get("artifact_input_discovery") or {}
    alias = params.get("input_alias")
    candidate = next(
        (item for item in state.get("candidates", []) if item["candidate_id"] == params.get("candidate_id")), None
    )
    reason = params.get("reason")
    if alias not in state.get("missing", []) or candidate is None or not isinstance(reason, str) or not reason.strip():
        return {
            "success": False,
            "executed": False,
            "error": "Select a pending input_alias, candidate_id and explain the content match.",
        }
    source = Path(candidate["path"])
    inspected = str(source.resolve()) in state.get("read_paths", [])
    if source.is_dir():
        inspected = any(Path(p).is_relative_to(source.resolve()) for p in state.get("read_paths", []))
    if not inspected:
        return {"success": False, "executed": False, "error": "Read this candidate before confirming it."}
    if state.get("manifest_on_disk") and candidate.get("source_alias"):
        from .artifact_contracts import artifact_manifest_path
        from .artifact_versions import _read

        current = _read(artifact_manifest_path(node.plan_id, context.get("session_id")))
        current_entry = (current.get("artifacts") or {}).get(candidate["source_alias"]) or {}
        if (
            current_entry.get("artifact_version_id") != candidate.get("source_version_id")
            and current.get("schema_version") == 2
        ):
            return {
                "success": False,
                "executed": False,
                "error": "The published input version changed. Inspect the new version before continuing.",
            }
        migrated = (
            current.get("schema_version") != 2
            and candidate.get("source_version_id")
            and current_entry.get("path")
            and Path(current_entry["path"]).exists()
            and _sha(Path(current_entry["path"])) == candidate["sha256"]
        )
        if not migrated and (
            not current_entry.get("path") or Path(current_entry["path"]).resolve() != source.resolve()
        ):
            return {"success": False, "executed": False, "error": "The published input path changed or disappeared."}
    record = {
        **candidate,
        "plan_id": node.plan_id,
        "task_id": node.id,
        "definition": definition(node),
        "source_path": str(source),
        "reason": reason[:2000],
    }
    if not existing_bindings(node, manifest, {"input_artifact_bindings": {alias: record}}):
        return {
            "success": False,
            "executed": False,
            "error": "Candidate changed or is no longer available; inspect the current input again.",
        }
    from .artifact_versions import active_binding, snapshot
    from .artifact_contracts import canonical_plan_root

    active = active_binding.get()
    if active:
        if active.plan_id != node.plan_id or active.task_id != node.id:
            return {"success": False, "executed": False, "error": "Input binding scope mismatch."}
        blob, immutable = snapshot(source, canonical_plan_root(node.plan_id, context.get("session_id")))
        active.source_inputs[alias] = {"source": str(source), "blob_id": blob, "path": immutable}
        source_alias = candidate.get("source_alias")
        if source_alias:
            from .artifact_contracts import artifact_manifest_path
            from .artifact_versions import _read

            current = _read(artifact_manifest_path(node.plan_id, context.get("session_id")))
            active.expected_inputs[source_alias] = (current.get("artifacts", {}).get(source_alias) or {}).get(
                "artifact_version_id"
            )
            if candidate.get("source_version_id"):
                active.inputs[source_alias] = candidate["source_version_id"]
        record["path"] = immutable
    state["bindings"][alias] = record
    context.setdefault("resolved_input_artifacts", {})[alias] = record["path"]
    return {
        "success": True,
        "operation": "resolve_input",
        "input_alias": alias,
        "path": record["path"],
        "input_artifact_binding": record,
        "remaining_inputs": pending(context),
    }


def attach(context, payload):
    records = dict(context.get("input_artifact_bindings") or {})
    records.update((context.get("artifact_input_discovery") or {}).get("bindings") or {})
    if records:
        payload.setdefault("metadata", {})["input_artifact_bindings"] = records


def restore_receipt(context, node, manifest, result):
    """Reconstruct the scoped binding when the existing step ledger replays a receipt."""
    state = context.get("artifact_input_discovery")
    for _ in range(3):
        if isinstance(result.get("result"), dict) and "operation" not in result:
            result = result["result"]
    if not state or not result.get("success") or result.get("operation") != "resolve_input":
        return
    alias, record = result.get("input_alias"), result.get("input_artifact_binding")
    if alias not in state.get("missing", []):
        return
    records = existing_bindings(node, manifest, {"input_artifact_bindings": {alias: record}})
    if records:
        state["bindings"].update(records)
