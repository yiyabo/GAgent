"""Batch download collection for artifact routes.

Resolves the requested raw/deliverable file entries to (path, arcname) pairs
for the zip streaming endpoint, enforcing path containment, hidden-artifact
prefixes and per-entry error codes. The streaming endpoint itself stays in the
facade.

``_resolve_session_dir`` and ``_load_hidden_artifact_prefixes`` are
facade-re-exported and patched on the facade namespace
(test_artifact_batch_download.py:70/287/308, test_artifact_routes.py:206), so
they are read at call time via ``_facade()``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, status

from .deliverable_store import _path_is_hidden, _resolve_deliverable_view
from .schemas import BatchDownloadFileEntry
from .session_dirs import _assert_path_within


def _facade() -> Any:
    """Return the package facade module (late-bound, patch-safe)."""
    from .. import artifact_routes as facade

    return facade


def _collect_batch_files(
    session_id: str,
    files: List[BatchDownloadFileEntry],
) -> List[Tuple[Path, str]]:
    raw_session_dir: Optional[Path] = None
    deliverable_session_dir: Optional[Path] = None
    hidden_prefixes: Optional[List[str]] = None
    deliverable_view_cache: Dict[str, Path] = {}

    collected: List[Tuple[Path, str]] = []
    for entry in files:
        raw_path = str(entry.path or "").strip()
        if not raw_path:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File path is required",
            )
        normalized_path = raw_path.lstrip("/").replace("\\", "/")

        if entry.scope == "raw":
            if raw_session_dir is None:
                raw_session_dir = _facade()._resolve_session_dir(session_id, purpose="raw")
            if hidden_prefixes is None:
                hidden_prefixes = _facade()._load_hidden_artifact_prefixes(session_id)

            target = (raw_session_dir / normalized_path).resolve()
            try:
                _assert_path_within(target, raw_session_dir, detail="Invalid artifact path")
            except HTTPException as exc:
                if exc.status_code == status.HTTP_400_BAD_REQUEST:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Invalid artifact path",
                    )
                raise
            rel_path = str(target.relative_to(raw_session_dir)).replace("\\", "/")
            if _path_is_hidden(rel_path, hidden_prefixes):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Artifact is hidden",
                )
            if not target.exists() or not target.is_file():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Artifact not found",
                )
            arcname = rel_path
        else:
            if deliverable_session_dir is None:
                deliverable_session_dir = _facade()._resolve_session_dir(session_id, purpose="deliverables")
            if hidden_prefixes is None:
                hidden_prefixes = _facade()._load_hidden_artifact_prefixes(session_id)

            version_key = entry.version or ""
            if version_key not in deliverable_view_cache:
                _, _, files_root, _, _ = _resolve_deliverable_view(
                    session_dir=deliverable_session_dir,
                    scope="history" if entry.version else "latest",
                    version=entry.version,
                )
                deliverable_view_cache[version_key] = files_root

            files_root = deliverable_view_cache[version_key]
            resolved_files_root = files_root.resolve()
            target = (files_root / normalized_path).resolve()
            try:
                _assert_path_within(target, resolved_files_root, detail="Invalid deliverable path")
            except HTTPException as exc:
                if exc.status_code == status.HTTP_400_BAD_REQUEST:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Invalid deliverable path",
                    )
                raise
            rel_for_hidden = str(target.relative_to(resolved_files_root)).replace("\\", "/")
            if _path_is_hidden(rel_for_hidden, hidden_prefixes):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Deliverable is hidden",
                )
            if not target.exists() or not target.is_file():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Deliverable file not found",
                )
            arcname = str(target.relative_to(deliverable_session_dir)).replace("\\", "/")

        collected.append((target, arcname))

    return collected
