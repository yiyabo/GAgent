"""Runtime-context / subject-tracking cluster of ``action_handlers``.

Moved verbatim out of ``action_handlers.py`` per
design/2026-09-24-backend-godfiles-refactor-plan.md §4.7 (handlers cluster ②,
the ``:803-1145`` run in the pre-split file): runtime-context persistence,
current-turn lookup, local-subject inference, subject extraction from a tool
call, local tool-param path normalization, subject action-class inference,
produced-artifact collection and the end-of-tool runtime-context update.

D1 convergence (W0) is *not* re-done here: ``app/routers/chat/agent.py`` now
imports the single source of truth from ``action_handlers`` and
``app/tests/chat/test_persist_runtime_context.py:46`` asserts
``agent._persist_runtime_context is action_handlers._persist_runtime_context``.
The facade re-exports every name moved here, so that identity and every import
site are unchanged.

Late binding: ``_persist_runtime_context`` calls
``_update_session_metadata(...)``, which
``app/tests/chat/test_persist_runtime_context.py:41,74`` patches **on the
action_handlers namespace** — so it is reached through ``_ah()`` (the facade
module) at call time.  That is the only deviation from byte-verbatim here (one
line).

The module uses its own ``logging.getLogger(__name__)`` (split precedent); the
"Failed to persist runtime context" message text is unchanged.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .artifact_gallery import update_recent_image_artifacts
from .subject_identity import (
    build_subject_aliases,
    canonicalize_subject_ref,
    normalize_tool_path,
    subject_identity_matches,
)

logger = logging.getLogger(__name__)

_RUNTIME_CONTEXT_KEYS = (
    "active_subject",
    "last_failure_state",
    "last_evidence_state",
    "last_subject_action_class",
    "recent_image_artifacts",
)
_MUTATING_FILE_OPERATIONS = {"write", "copy", "move", "delete"}
_LOCAL_SUBJECT_TOOLS = {
    "file_operations",
    "document_reader",
    "vision_reader",
    "result_interpreter",
    "code_executor",
    "terminal_session",
}


def _ah() -> Any:
    """Late-bound action_handlers facade module (monkeypatch-friendly lookups)."""
    from . import action_handlers

    return action_handlers


def _persist_runtime_context(agent: Any) -> None:
    if not getattr(agent, "session_id", None):
        return

    def _updater(metadata: Dict[str, Any]) -> Dict[str, Any]:
        for key in _RUNTIME_CONTEXT_KEYS:
            value = (getattr(agent, "extra_context", {}) or {}).get(key)
            if isinstance(value, dict):
                metadata[key] = dict(value)
            elif isinstance(value, list) and key == "recent_image_artifacts":
                metadata[key] = [dict(item) for item in value if isinstance(item, dict)]
            else:
                metadata.pop(key, None)
        return metadata

    _ah()._update_session_metadata(agent.session_id, _updater)


def _current_user_turn(agent: Any) -> int:
    try:
        return int((getattr(agent, "extra_context", {}) or {}).get("current_user_turn_index") or 1)
    except (TypeError, ValueError):
        return 1


def _infer_subject_kind(path_text: str, operation: Optional[str] = None) -> str:
    candidate = str(path_text or "").strip()
    if not candidate:
        return "workspace"
    if operation == "list" or candidate.endswith("/"):
        return "directory"
    basename = candidate.rstrip("/").rsplit("/", 1)[-1]
    if "." in basename:
        return "file"
    return "workspace"


def _extract_subject_from_tool_call(
    tool_name: str,
    params: Dict[str, Any],
    *,
    active_subject: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    if tool_name == "file_operations":
        path = str(params.get("path") or "").strip()
        if path:
            operation = str(params.get("operation") or "").strip().lower()
            canonical_ref = normalize_tool_path(path, active_subject=active_subject)
            return {
                "canonical_ref": canonical_ref or path,
                "display_ref": path,
                "kind": _infer_subject_kind(path, operation),
                "last_tool_scope": operation or tool_name,
                "aliases": build_subject_aliases(path, canonical_ref),
            }
    if tool_name == "document_reader":
        path = str(params.get("file_path") or "").strip()
        if path:
            canonical_ref = normalize_tool_path(path, active_subject=active_subject)
            return {
                "canonical_ref": canonical_ref or path,
                "display_ref": path,
                "kind": "file",
                "last_tool_scope": str(params.get("operation") or tool_name).strip() or tool_name,
                "aliases": build_subject_aliases(path, canonical_ref),
            }
    if tool_name == "vision_reader":
        path = str(params.get("image_path") or params.get("file_path") or "").strip()
        if path:
            canonical_ref = normalize_tool_path(path, active_subject=active_subject)
            return {
                "canonical_ref": canonical_ref or path,
                "display_ref": path,
                "kind": "file",
                "last_tool_scope": str(params.get("operation") or tool_name).strip() or tool_name,
                "aliases": build_subject_aliases(path, canonical_ref),
            }
    if tool_name == "result_interpreter":
        file_paths = params.get("file_paths")
        path = ""
        if isinstance(file_paths, list) and file_paths:
            path = str(file_paths[0] or "").strip()
        elif isinstance(params.get("file_path"), str):
            path = str(params.get("file_path") or "").strip()
        if path:
            canonical_ref = normalize_tool_path(path, active_subject=active_subject)
            return {
                "canonical_ref": canonical_ref or path,
                "display_ref": path,
                "kind": "file",
                "last_tool_scope": str(params.get("operation") or tool_name).strip() or tool_name,
                "aliases": build_subject_aliases(path, canonical_ref),
            }
    return None


def _normalize_local_tool_params(agent: Any, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    active_subject = (
        dict((getattr(agent, "extra_context", {}) or {}).get("active_subject") or {})
        if isinstance((getattr(agent, "extra_context", {}) or {}).get("active_subject"), dict)
        else None
    )
    normalized = dict(params)

    if tool_name == "file_operations":
        if isinstance(normalized.get("path"), str):
            normalized["path"] = normalize_tool_path(
                normalized.get("path"),
                active_subject=active_subject,
            )
        if isinstance(normalized.get("destination"), str):
            normalized["destination"] = canonicalize_subject_ref(normalized.get("destination"))
    elif tool_name == "document_reader" and isinstance(normalized.get("file_path"), str):
        normalized["file_path"] = normalize_tool_path(
            normalized.get("file_path"),
            active_subject=active_subject,
        )
    elif tool_name == "vision_reader":
        if isinstance(normalized.get("image_path"), str):
            normalized["image_path"] = normalize_tool_path(
                normalized.get("image_path"),
                active_subject=active_subject,
            )
        if isinstance(normalized.get("file_path"), str):
            normalized["file_path"] = normalize_tool_path(
                normalized.get("file_path"),
                active_subject=active_subject,
            )
    elif tool_name == "result_interpreter":
        if isinstance(normalized.get("file_path"), str):
            normalized["file_path"] = normalize_tool_path(
                normalized.get("file_path"),
                active_subject=active_subject,
            )
        file_paths = normalized.get("file_paths")
        if isinstance(file_paths, list):
            normalized["file_paths"] = [
                normalize_tool_path(item, active_subject=active_subject) if isinstance(item, str) else item
                for item in file_paths
            ]
    return normalized


def _infer_subject_action_class(
    *,
    tool_name: str,
    params: Dict[str, Any],
    extra_context: Dict[str, Any],
    success: bool,
    sanitized: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    if not success:
        return None
    if tool_name == "file_operations":
        operation = str(params.get("operation") or "").strip().lower()
        if operation == "list":
            return "inspect"
        if operation in {"read", "exists", "info"}:
            return "read_only"
        if operation in _MUTATING_FILE_OPERATIONS:
            return "mutation"
        return None
    if tool_name in {"document_reader", "vision_reader", "result_interpreter"}:
        return "inspect"
    if tool_name == "terminal_session":
        operation = str(params.get("operation") or "").strip().lower()
        if operation in {"replay", "list"}:
            return "inspect"
    return None


def _collect_produced_artifacts(
    sanitized: Dict[str, Any],
    storage_info: Any = None,
) -> List[str]:
    produced: List[str] = []
    storage_payload = sanitized.get("storage") if isinstance(sanitized, dict) else None
    if isinstance(storage_payload, dict):
        for key in ("output_dir", "result_path", "manifest_path", "preview_path"):
            value = storage_payload.get(key)
            if isinstance(value, str) and value.strip():
                produced.append(value.strip())
    if storage_info is not None:
        for key in ("output_dir", "result_path", "manifest_path", "preview_path"):
            value = getattr(storage_info, key, None)
            if isinstance(value, str) and value.strip():
                produced.append(value.strip())
    deduped: List[str] = []
    seen = set()
    for item in produced:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _update_runtime_context_from_tool(
    agent: Any,
    *,
    tool_name: str,
    params: Dict[str, Any],
    sanitized: Dict[str, Any],
    summary: str,
    storage_info: Any = None,
) -> None:
    extra_context = getattr(agent, "extra_context", {}) or {}
    current_turn = _current_user_turn(agent)
    subject = _extract_subject_from_tool_call(
        tool_name,
        params,
        active_subject=extra_context.get("active_subject") if isinstance(extra_context.get("active_subject"), dict) else None,
    )
    active_subject = extra_context.get("active_subject")
    if not isinstance(active_subject, dict):
        active_subject = {}
    subject_ref = ""
    subject_kind = "workspace"
    display_ref = ""
    subject_aliases: List[str] = []
    last_tool_scope = tool_name
    if isinstance(subject, dict):
        subject_ref = canonicalize_subject_ref(
            subject.get("canonical_ref") or subject.get("display_ref")
        )
        subject_kind = str(subject.get("kind") or "workspace").strip() or "workspace"
        display_ref = str(subject.get("display_ref") or subject_ref).strip() or subject_ref
        last_tool_scope = str(subject.get("last_tool_scope") or tool_name).strip() or tool_name
    elif tool_name in _LOCAL_SUBJECT_TOOLS:
        subject_ref = canonicalize_subject_ref(
            active_subject.get("canonical_ref") or active_subject.get("display_ref")
        )
        subject_kind = str(active_subject.get("kind") or "workspace").strip() or "workspace"
        display_ref = str(active_subject.get("display_ref") or subject_ref).strip() or subject_ref
        last_tool_scope = str(active_subject.get("last_tool_scope") or tool_name).strip() or tool_name

    success = sanitized.get("success") is not False
    error_message = str(
        sanitized.get("error") or sanitized.get("message") or summary or "unknown error"
    ).strip()
    verification_state = "verified"
    lowered_error = error_message.lower()
    if not success:
        if "not found" in lowered_error or "不存在" in error_message:
            verification_state = "not_found"
        else:
            verification_state = "failed"

    if subject_ref:
        subject_aliases = build_subject_aliases(
            subject.get("aliases") if isinstance(subject, dict) else None,
            subject_ref,
            display_ref,
        )
        same_subject = subject_identity_matches(
            active_subject,
            candidate_ref=subject_ref,
            candidate_display_ref=display_ref,
            candidate_aliases=subject_aliases,
        )
        existing = active_subject if same_subject else {}
        extra_context["active_subject"] = {
            "kind": subject_kind,
            "canonical_ref": subject_ref,
            "display_ref": (
                str(existing.get("display_ref") or "").strip() if same_subject else display_ref
            ) or display_ref or subject_ref,
            "aliases": build_subject_aliases(
                existing.get("aliases") if same_subject else None,
                subject_aliases,
                subject_ref,
                display_ref,
            ),
            "verification_state": verification_state,
            "salience": 5,
            "last_tool_scope": last_tool_scope,
            "created_turn": existing.get("created_turn") or current_turn,
            "last_referenced_turn": current_turn,
            "last_verified_turn": current_turn if success else existing.get("last_verified_turn"),
        }

    produced_artifacts = _collect_produced_artifacts(sanitized, storage_info=storage_info)
    if isinstance(sanitized.get("artifact_gallery"), list):
        update_recent_image_artifacts(
            extra_context,
            sanitized.get("artifact_gallery"),
        )
    if success:
        extra_context["last_evidence_state"] = {
            "status": "verified" if subject_ref else "success",
            "verified_facts": [summary] if summary else [],
            "produced_artifacts": produced_artifacts,
            "unresolved": [],
            "timestamp": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        }
        if subject_ref:
            failure_state = extra_context.get("last_failure_state")
            if isinstance(failure_state, dict) and subject_identity_matches(
                {
                    "canonical_ref": failure_state.get("subject_ref"),
                    "display_ref": failure_state.get("subject_ref"),
                    "aliases": failure_state.get("subject_aliases"),
                },
                candidate_ref=subject_ref,
                candidate_display_ref=display_ref,
                candidate_aliases=subject_aliases,
            ):
                extra_context.pop("last_failure_state", None)
        action_class = _infer_subject_action_class(
            tool_name=tool_name,
            params=params,
            extra_context=extra_context,
            success=success,
            sanitized=sanitized,
        )
        if action_class:
            extra_context["last_subject_action_class"] = action_class
    else:
        if subject_ref:
            extra_context["last_failure_state"] = {
                "subject_ref": subject_ref,
                "subject_aliases": subject_aliases,
                "tool_name": tool_name,
                "operation": str(params.get("operation") or tool_name).strip() or tool_name,
                "error_message": error_message,
                "timestamp": datetime.now(timezone(timedelta(hours=8))).isoformat(),
            }
        extra_context["last_evidence_state"] = {
            "status": "failed",
            "verified_facts": [],
            "produced_artifacts": produced_artifacts,
            "unresolved": [error_message] if error_message else [],
            "timestamp": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        }

    agent.extra_context = extra_context
    if getattr(agent, "session_id", None):
        try:
            _persist_runtime_context(agent)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Failed to persist runtime context: %s", exc)
