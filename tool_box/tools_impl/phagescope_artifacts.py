"""PhageScope artifact location and local-output helpers."""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ARTIFACT_SCOPE_API_ONLY = "api_response_only"
LOCAL_BUNDLE_HINT_EN = (
    "This call returns API/JSON (or one artifact) only. "
    "For a full local bundle (metadata/, annotation/, raw_api_responses/, etc.), use action=save_all with the same numeric taskid."
)


def _with_api_only_artifact_hint(result: Dict[str, Any], taskid: Optional[str] = None) -> Dict[str, Any]:
    if not result.get("success"):
        return result
    action = str(result.get("action") or "").strip().lower()
    if action in {"save_all", "ping", "submit", "task_list", "input_check", "cluster_submit"}:
        return result
    if action not in {"result", "task_detail", "task_log", "download", "quality", "query"}:
        return result
    out = dict(result)
    out["artifact_scope"] = ARTIFACT_SCOPE_API_ONLY
    out["local_bundle_hint"] = LOCAL_BUNDLE_HINT_EN
    tid = taskid if taskid is not None else out.get("taskid")
    if tid:
        out["taskid"] = str(tid)
    return out


def _resolve_session_phagescope_root(session_id: Optional[str]) -> Optional[Path]:
    """Resolve runtime/session_<id>/work/phagescope for session-scoped saves."""
    token = str(session_id or "").strip()
    if not token:
        return None
    try:
        from app.services.session_paths import get_session_phagescope_work_dir

        return get_session_phagescope_work_dir(token, create=True)
    except Exception as exc:
        logger.warning("Failed to resolve session-scoped PhageScope root for %s: %s", token, exc)
        return None


def _resolve_session_root(session_id: Optional[str]) -> Optional[Path]:
    token = str(session_id or "").strip()
    if not token:
        return None
    try:
        from app.services.session_paths import get_runtime_session_dir

        return get_runtime_session_dir(token, create=True)
    except Exception as exc:
        logger.warning("Failed to resolve session root for %s: %s", token, exc)
        return None


def _dedupe_string_list(items: List[str]) -> List[str]:
    deduped: List[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped


def _session_relative_path(path: Path, session_id: Optional[str]) -> Optional[str]:
    session_root = _resolve_session_root(session_id)
    if session_root is None:
        return None
    try:
        return str(path.resolve().relative_to(session_root.resolve())).replace("\\", "/")
    except Exception:
        return None


def _attach_output_location_fields(
    result: Dict[str, Any],
    *,
    base_dir: Optional[Path],
    session_id: Optional[str] = None,
    task_id: Optional[int] = None,
    ancestor_chain: Optional[List[int]] = None,
) -> Dict[str, Any]:
    if base_dir is None:
        return result
    resolved_base = base_dir.expanduser().resolve()
    out = dict(result)
    session_artifact_paths = [str(item) for item in list(out.get("session_artifact_paths") or []) if str(item).strip()]
    if not session_artifact_paths and resolved_base.exists() and resolved_base.is_dir():
        for candidate in sorted(resolved_base.rglob("*")):
            if not candidate.is_file():
                continue
            rel_path = _session_relative_path(candidate, session_id)
            session_artifact_paths.append(rel_path or str(candidate.resolve()))
    if session_artifact_paths:
        out["session_artifact_paths"] = _dedupe_string_list(session_artifact_paths)
    out["output_location"] = {
        "type": "task" if task_id is not None else "tmp",
        "session_id": session_id,
        "task_id": task_id,
        "ancestor_chain": ancestor_chain,
        "base_dir": str(resolved_base),
        "files": list(out.get("session_artifact_paths") or []),
    }
    return out


def _attach_local_file_artifact_fields(
    result: Dict[str, Any],
    *,
    local_path: Path,
    session_id: Optional[str] = None,
    task_id: Optional[int] = None,
    ancestor_chain: Optional[List[int]] = None,
    output_base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    resolved = local_path.expanduser().resolve()
    out = dict(result)
    out["saved_path"] = str(resolved)
    out["output_file"] = str(resolved)
    artifact_paths = list(out.get("artifact_paths") or [])
    artifact_paths.append(str(resolved))
    out["artifact_paths"] = _dedupe_string_list([str(item) for item in artifact_paths])
    rel_path = _session_relative_path(resolved, session_id)
    if rel_path:
        out["saved_path_rel"] = rel_path
        out["output_file_rel"] = rel_path
        session_artifact_paths = list(out.get("session_artifact_paths") or [])
        session_artifact_paths.append(rel_path)
        out["session_artifact_paths"] = _dedupe_string_list([str(item) for item in session_artifact_paths])
    return _attach_output_location_fields(
        out,
        base_dir=output_base_dir or resolved.parent,
        session_id=session_id,
        task_id=task_id,
        ancestor_chain=ancestor_chain,
    )


def _attach_local_bundle_artifact_fields(
    result: Dict[str, Any],
    *,
    output_dir: Path,
    saved_files: Dict[str, str],
    summary_file: Optional[Path] = None,
    session_id: Optional[str] = None,
    task_id: Optional[int] = None,
    ancestor_chain: Optional[List[int]] = None,
) -> Dict[str, Any]:
    out = dict(result)
    artifact_paths: List[str] = [str(item) for item in list(out.get("artifact_paths") or [])]
    session_artifact_paths: List[str] = [str(item) for item in list(out.get("session_artifact_paths") or [])]
    candidates: List[Path] = []
    if summary_file is not None:
        candidates.append(summary_file.expanduser().resolve())
    for raw_path in saved_files.values():
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        candidate = Path(raw_path).expanduser()
        candidate = (output_dir / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        candidates.append(candidate)
    for candidate in candidates:
        artifact_paths.append(str(candidate))
        rel_path = _session_relative_path(candidate, session_id)
        if rel_path:
            session_artifact_paths.append(rel_path)
    if artifact_paths:
        out["artifact_paths"] = _dedupe_string_list(artifact_paths)
    if session_artifact_paths:
        out["session_artifact_paths"] = _dedupe_string_list(session_artifact_paths)
    return _attach_output_location_fields(
        out,
        base_dir=output_dir,
        session_id=session_id,
        task_id=task_id,
        ancestor_chain=ancestor_chain,
    )


__all__ = [
    "ARTIFACT_SCOPE_API_ONLY", "LOCAL_BUNDLE_HINT_EN", "_with_api_only_artifact_hint",
    "_resolve_session_phagescope_root", "_resolve_session_root", "_dedupe_string_list",
    "_session_relative_path", "_attach_output_location_fields", "_attach_local_file_artifact_fields",
    "_attach_local_bundle_artifact_fields",
]
