"""Project task-declared outputs without treating scratch files as deliverables.

This is a read-only view of the last verifier record. It does not publish a
manifest version or turn a partial task into a completed one.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.database import plan_db_connection
from app.repository.plan_storage import get_plan_db_path
from app.services.session_paths import get_runtime_session_dir

from .output_spec import output_spec_from_metadata


def _json(value):
    if not value:
        return {}
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def list_declared_outputs(plan_id: int, session_id: str | None, task_id: int | None = None, *, limit: int = 200):
    """Return only recorded files matching a task's declared target paths."""
    if not session_id:
        return [], False
    session_root = get_runtime_session_dir(session_id).resolve()
    where = " WHERE id=?" if task_id is not None else ""
    args = (task_id,) if task_id is not None else ()
    with plan_db_connection(get_plan_db_path(plan_id)) as conn:
        rows = conn.execute(
            "SELECT id,status,metadata, "
            "CASE WHEN json_valid(execution_result) THEN "
            "json_extract(execution_result,'$.metadata.output_verification') END AS report, "
            "CASE WHEN json_valid(execution_result) THEN "
            "json_extract(execution_result,'$.metadata.output_spec') END AS result_spec, "
            "CASE WHEN json_valid(execution_result) THEN "
            "json_extract(execution_result,'$.metadata.output_spec_base_dir') END AS result_base "
            "FROM tasks" + where + " ORDER BY id", args,
        ).fetchall()
    items = []
    seen = set()
    truncated = False
    for row in rows:
        metadata = _json(row["metadata"])
        try:
            spec = output_spec_from_metadata(metadata)
            if spec is None or not spec.required_outputs:
                spec = output_spec_from_metadata({"output_spec": _json(row["result_spec"])})
        except ValueError:
            continue
        if spec is None or not spec.required_outputs:
            continue
        report = _json(row["report"])
        if not isinstance(report.get("artifact_paths"), list):
            continue
        raw_base = row["result_base"] or metadata.get("output_spec_base_dir")
        if not isinstance(raw_base, str) or not raw_base:
            continue
        base = Path(raw_base).resolve()
        if not base.is_relative_to(session_root) or base.name != f"task_{row['id']}":
            continue
        accepted = row["status"] in {"completed", "done"} and report.get("status") == "passed"
        counts = report.get("matched_counts") if isinstance(report.get("matched_counts"), list) else []
        for output_index, output in enumerate(spec.required_outputs):
            if not output.target_path:
                continue
            target = Path(output.target_path)
            target = (target if target.is_absolute() else base / target).resolve()
            if not target.is_relative_to(base):
                continue
            directory = output.target_path.endswith("/") or (not target.suffix and target.is_dir())
            for raw_path in report["artifact_paths"]:
                if not isinstance(raw_path, str) or not raw_path:
                    continue
                path = Path(raw_path).resolve()
                if not path.is_relative_to(base) or not path.is_file():
                    continue
                if directory:
                    if not path.is_relative_to(target) or path == target:
                        continue
                elif path != target:
                    continue
                if output.extensions and not any(path.name.lower().endswith(ext) for ext in output.extensions):
                    continue
                key = (row["id"], str(path))
                if key in seen:
                    continue
                seen.add(key)
                if len(items) >= limit:
                    truncated = True
                    break
                items.append({
                    "alias": f"declared:{row['id']}:{path.relative_to(base).as_posix()}",
                    "producer_task_id": row["id"],
                    "session_id": session_id,
                    "path": str(path),
                    "origin": "declared_output",
                    "output_status": "accepted" if accepted else "partial",
                    "validated": bool(accepted),
                    "required_count": output.min_count,
                    "matched_count": counts[output_index] if output_index < len(counts) and isinstance(counts[output_index], int) else None,
                })
            if truncated:
                break
        if truncated:
            break
    return items, truncated
