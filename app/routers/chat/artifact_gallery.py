"""Helpers for session-scoped image artifact galleries."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from app.services.upload_storage import get_session_root_dir
from .subject_identity import _workspace_root

_IMAGE_EXT_RE = re.compile(r"\.(png|jpe?g|gif|webp|svg)$", re.IGNORECASE)
_DEFAULT_GALLERY_LIMIT = 12


def is_image_artifact_path(path: Any) -> bool:
    text = str(path or "").strip()
    if not text:
        return False
    return bool(_IMAGE_EXT_RE.search(text))


def _default_created_at(value: Optional[str] = None) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_session_relative_path(
    path: Any,
    *,
    session_id: Optional[str],
) -> Optional[str]:
    raw = str(path or "").strip()
    if not raw:
        return None
    normalized = raw.replace("\\", "/")
    if "://" in normalized:
        return None
    if not is_image_artifact_path(normalized):
        return None

    try:
        parts = Path(normalized.lstrip("/")).parts
    except Exception:
        parts = ()
    if ".." in parts:
        return None

    if normalized.startswith("/"):
        session_root: Optional[Path]
        try:
            session_root = get_session_root_dir(str(session_id or "").strip()).resolve()
        except Exception:
            session_root = None
        if session_root is None:
            return None
        try:
            candidate = Path(normalized).resolve()
            relative = candidate.relative_to(session_root)
        except Exception:
            return None
        return str(relative).replace("\\", "/")

    return normalized.lstrip("/")


def _normalize_workspace_absolute_path(path: Any) -> Optional[str]:
    raw = str(path or "").strip()
    if not raw:
        return None
    normalized = raw.replace("\\", "/")
    if "://" in normalized or not is_image_artifact_path(normalized):
        return None

    # Recover historical frontend-normalized absolute paths like "Users/foo/bar.png".
    if not normalized.startswith("/") and re.match(
        r"^(Users|home|private|Volumes|tmp|var|opt|workspace|workspaces|data|mnt|srv|root)/",
        normalized,
    ):
        normalized = "/" + normalized

    if not normalized.startswith("/"):
        return None

    try:
        workspace_root = _workspace_root().resolve()
        candidate = Path(normalized).expanduser().resolve(strict=False)
        candidate.relative_to(workspace_root)
    except Exception:
        return None
    return str(candidate).replace("\\", "/")


def _infer_origin(path: str, preferred: Optional[str] = None) -> str:
    origin = str(preferred or "").strip().lower()
    if origin in {"artifact", "deliverable", "markdown", "workspace"}:
        return origin
    normalized = path.replace("\\", "/").lower()
    if normalized.startswith("/"):
        return "workspace"
    if normalized.startswith("deliverables/") or "/deliverables/" in normalized:
        return "deliverable"
    return "artifact"


def _display_name_for_path(path: str, preferred: Optional[str] = None) -> str:
    name = str(preferred or "").strip()
    if name:
        return name
    return os.path.basename(path.rstrip("/")) or path


def build_artifact_gallery_item(
    path: Any,
    *,
    session_id: Optional[str],
    source_tool: Optional[str] = None,
    tracking_id: Optional[str] = None,
    created_at: Optional[str] = None,
    display_name: Optional[str] = None,
    origin: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    normalized_path = _normalize_session_relative_path(path, session_id=session_id)
    if not normalized_path:
        normalized_path = _normalize_workspace_absolute_path(path)
    if not normalized_path:
        return None
    return {
        "path": normalized_path,
        "display_name": _display_name_for_path(normalized_path, display_name),
        "source_tool": str(source_tool or "unknown").strip() or "unknown",
        "mime_family": "image",
        "origin": _infer_origin(normalized_path, origin),
        "created_at": _default_created_at(created_at),
        "tracking_id": str(tracking_id or "").strip() or None,
    }


def merge_artifact_gallery(
    existing: Optional[Sequence[Dict[str, Any]]],
    additions: Optional[Sequence[Dict[str, Any]]],
    *,
    limit: int = _DEFAULT_GALLERY_LIMIT,
) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def _push(item: Any) -> None:
        if not isinstance(item, dict):
            return
        path = str(item.get("path") or "").strip()
        if not path or not is_image_artifact_path(path):
            return
        origin = str(item.get("origin") or _infer_origin(path)).strip().lower() or "artifact"
        key = (origin, path)
        if key in seen:
            return
        seen.add(key)
        merged.append(
            {
                "path": path,
                "display_name": _display_name_for_path(path, item.get("display_name")),
                "source_tool": str(item.get("source_tool") or "unknown").strip() or "unknown",
                "mime_family": "image",
                "origin": origin,
                "created_at": _default_created_at(item.get("created_at")),
                "tracking_id": str(item.get("tracking_id") or "").strip() or None,
            }
        )

    for item in additions or []:
        _push(item)
    for item in existing or []:
        _push(item)

    return merged[: max(1, int(limit))]


def update_recent_image_artifacts(
    extra_context: Dict[str, Any],
    additions: Optional[Sequence[Dict[str, Any]]],
    *,
    limit: int = _DEFAULT_GALLERY_LIMIT,
) -> List[Dict[str, Any]]:
    existing = extra_context.get("recent_image_artifacts")
    existing_items = existing if isinstance(existing, list) else []
    merged = merge_artifact_gallery(existing_items, additions, limit=limit)
    if merged:
        extra_context["recent_image_artifacts"] = merged
    else:
        extra_context.pop("recent_image_artifacts", None)
    return merged


def _append_candidate_item(
    target: List[Dict[str, Any]],
    path: Any,
    *,
    session_id: Optional[str],
    source_tool: Optional[str],
    tracking_id: Optional[str],
    created_at: Optional[str],
    display_name: Optional[str] = None,
    origin: Optional[str] = None,
) -> None:
    item = build_artifact_gallery_item(
        path,
        session_id=session_id,
        source_tool=source_tool,
        tracking_id=tracking_id,
        created_at=created_at,
        display_name=display_name,
        origin=origin,
    )
    if item is not None:
        target.append(item)


def _extract_storage_candidates(storage_payload: Dict[str, Any]) -> List[tuple[str, Optional[str]]]:
    candidates: List[tuple[str, Optional[str]]] = []
    for container in (
        storage_payload,
        storage_payload.get("relative") if isinstance(storage_payload.get("relative"), dict) else None,
    ):
        if not isinstance(container, dict):
            continue
        for key in ("preview_path", "result_path", "output_file", "output_file_rel"):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append((value.strip(), None))
        for key in ("paths", "artifact_paths"):
            items = container.get(key)
            if not isinstance(items, list):
                continue
            for item in items[:40]:
                if isinstance(item, str) and item.strip():
                    candidates.append((item.strip(), None))
    return candidates


def extract_artifact_gallery_from_result(
    result: Any,
    *,
    session_id: Optional[str],
    source_tool: Optional[str] = None,
    tracking_id: Optional[str] = None,
    created_at: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if not isinstance(result, dict):
        return []

    collected: List[Dict[str, Any]] = []
    existing_gallery = result.get("artifact_gallery")
    if isinstance(existing_gallery, list):
        for item in existing_gallery:
            if not isinstance(item, dict):
                continue
            _append_candidate_item(
                collected,
                item.get("path"),
                session_id=session_id,
                source_tool=item.get("source_tool") or source_tool,
                tracking_id=item.get("tracking_id") or tracking_id,
                created_at=item.get("created_at") or created_at,
                display_name=item.get("display_name"),
                origin=item.get("origin"),
            )

    artifact_paths = result.get("artifact_paths")
    if isinstance(artifact_paths, list):
        for item in artifact_paths[:40]:
            _append_candidate_item(
                collected,
                item,
                session_id=session_id,
                source_tool=source_tool,
                tracking_id=tracking_id,
                created_at=created_at,
                origin="artifact",
            )

    items = result.get("items")
    if isinstance(items, list):
        for row in items[:200]:
            if not isinstance(row, dict):
                continue
            _append_candidate_item(
                collected,
                row.get("path"),
                session_id=session_id,
                source_tool=source_tool,
                tracking_id=tracking_id,
                created_at=created_at,
                display_name=row.get("name") or row.get("display_name"),
                origin="workspace" if str(row.get("path") or "").strip().startswith("/") else "artifact",
            )

    for key in ("image_path", "output_file", "output_file_rel", "preview_path"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            _append_candidate_item(
                collected,
                value,
                session_id=session_id,
                source_tool=source_tool,
                tracking_id=tracking_id,
                created_at=created_at,
                origin="artifact",
            )

    storage_payload = result.get("storage")
    if isinstance(storage_payload, dict):
        for path, display_name in _extract_storage_candidates(storage_payload):
            _append_candidate_item(
                collected,
                path,
                session_id=session_id,
                source_tool=source_tool,
                tracking_id=tracking_id,
                created_at=created_at,
                display_name=display_name,
                origin="artifact",
            )

    deliverables_payload = result.get("deliverables")
    if isinstance(deliverables_payload, dict):
        artifacts = deliverables_payload.get("artifacts")
        if isinstance(artifacts, list):
            for row in artifacts[:40]:
                if not isinstance(row, dict):
                    continue
                _append_candidate_item(
                    collected,
                    row.get("path") or row.get("relative_path"),
                    session_id=session_id,
                    source_tool=source_tool,
                    tracking_id=tracking_id,
                    created_at=created_at,
                    display_name=row.get("name") or row.get("display_name"),
                    origin="deliverable",
                )

    return merge_artifact_gallery(None, collected)
