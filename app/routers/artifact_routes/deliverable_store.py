"""Deliverable manifest/version read model for artifact routes.

Reads the ``deliverables/`` tree of a session: version history listing, the
resolved latest/history view, manifest-derived release metadata and hidden
artifact prefixes, the materialized item listing, and the raw-file walker.

``get_deliverable_settings`` is owned by the package facade (tests patch it on
the facade namespace: test_artifact_batch_download.py:331) and
``_resolve_session_dir`` is facade-re-exported and patched as well
(test_artifact_routes.py:206), so both are read at call time via ``_facade()``;
the session-path helpers are imported directly from ``.session_dirs``.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, status

from .schemas import ArtifactItem, DeliverableItem, DeliverableVersionSummary
from .session_dirs import (
    _deliverables_history_dir,
    _deliverables_latest_dir,
    _deliverables_root,
)


def _facade() -> Any:
    """Return the package facade module (late-bound, patch-safe)."""
    from .. import artifact_routes as facade

    return facade


def _safe_json_load(path: Path) -> Dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(payload, dict):
        return payload
    return {}


def _list_deliverable_versions(
    *,
    history_root: Path,
    limit: int = 200,
) -> List[DeliverableVersionSummary]:
    if _facade().get_deliverable_settings().single_version_only:
        return []
    if not history_root.exists() or not history_root.is_dir():
        return []

    versions: List[DeliverableVersionSummary] = []
    for version_dir in sorted(
        [item for item in history_root.iterdir() if item.is_dir()],
        key=lambda item: item.name,
        reverse=True,
    )[:limit]:
        manifest = _safe_json_load(version_dir / "manifest.json")
        version_id = str(manifest.get("version_id") or version_dir.name)
        created_at = manifest.get("created_at")
        published_files_count = int(manifest.get("published_files_count") or 0)
        published_modules = manifest.get("published_modules") or []
        if not isinstance(published_modules, list):
            published_modules = []
        versions.append(
            DeliverableVersionSummary(
                version_id=version_id,
                created_at=created_at if isinstance(created_at, str) else None,
                published_files_count=published_files_count,
                published_modules=[str(item) for item in published_modules if item is not None],
            )
        )
    return versions


def _resolve_deliverable_view(
    *,
    session_dir: Path,
    scope: str,
    version: Optional[str],
) -> Tuple[str, Optional[str], Path, Path, Dict[str, Any]]:
    settings = _facade().get_deliverable_settings()
    normalized_scope = (scope or "latest").strip().lower()
    if normalized_scope not in {"latest", "history"}:
        normalized_scope = "latest"

    deliverables_root = _deliverables_root(session_dir)
    latest_root = _deliverables_latest_dir(session_dir)
    history_root = _deliverables_history_dir(session_dir)

    explicit_version = (version or "").strip()
    if settings.single_version_only:
        explicit_version = ""
        normalized_scope = "latest"

    if explicit_version:
        version_dir = (history_root / explicit_version).resolve()
        if not version_dir.exists() or not version_dir.is_dir():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deliverable version not found")
        manifest_path = version_dir / "manifest.json"
        manifest = _safe_json_load(manifest_path)
        resolved_version = str(manifest.get("version_id") or explicit_version)
        return "history", resolved_version, version_dir, manifest_path, manifest

    if normalized_scope == "history":
        candidate_versions = sorted(
            [item for item in history_root.iterdir() if item.is_dir()],
            key=lambda item: item.name,
            reverse=True,
        ) if history_root.exists() else []
        if candidate_versions:
            active = candidate_versions[0]
            manifest_path = active / "manifest.json"
            manifest = _safe_json_load(manifest_path)
            resolved_version = str(manifest.get("version_id") or active.name)
            return "history", resolved_version, active, manifest_path, manifest

    manifest_path = deliverables_root / "manifest_latest.json"
    manifest = _safe_json_load(manifest_path)
    resolved_version = manifest.get("version_id")
    if not isinstance(resolved_version, str):
        resolved_version = None
    return "latest", resolved_version, latest_root, manifest_path, manifest


def _paper_status_from_manifest(manifest: Dict[str, Any]) -> Dict[str, Any]:
    paper_status = manifest.get("paper_status")
    if isinstance(paper_status, dict):
        return paper_status
    return {
        "completed_sections": [],
        "missing_sections": [],
        "total_sections": 0,
        "completed_count": 0,
    }


def _release_meta_from_manifest(manifest: Dict[str, Any]) -> Dict[str, Any]:
    release_state = str(manifest.get("release_state") or "final").strip().lower() or "final"
    public_release_ready = manifest.get("public_release_ready")
    if public_release_ready is None:
        public_release_ready = release_state != "blocked"
    hidden_artifact_prefixes: List[str] = []
    values = manifest.get("hidden_artifact_prefixes")
    if isinstance(values, list):
        for item in values:
            normalized = str(item or "").strip().lstrip("/").replace("\\", "/")
            if normalized and normalized not in hidden_artifact_prefixes:
                hidden_artifact_prefixes.append(normalized)
    release_summary = manifest.get("release_summary")
    return {
        "release_state": release_state,
        "public_release_ready": bool(public_release_ready),
        "release_summary": release_summary if isinstance(release_summary, str) and release_summary.strip() else None,
        "hidden_artifact_prefixes": hidden_artifact_prefixes,
    }


def _path_is_hidden(path: str, hidden_prefixes: List[str]) -> bool:
    normalized = str(path or "").strip().lstrip("/").replace("\\", "/")
    if not normalized:
        return False
    for prefix in hidden_prefixes:
        candidate = str(prefix or "").strip().lstrip("/").replace("\\", "/")
        if not candidate:
            continue
        if normalized == candidate or normalized.startswith(candidate.rstrip("/") + "/"):
            return True
    return False


def _load_hidden_artifact_prefixes(session_id: str) -> List[str]:
    try:
        session_dir = _facade()._resolve_session_dir(session_id, purpose="deliverables")
    except HTTPException:
        return []
    manifest = _safe_json_load(_deliverables_root(session_dir) / "manifest_latest.json")
    return list(_release_meta_from_manifest(manifest).get("hidden_artifact_prefixes") or [])


def _manifest_items(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    direct_items = manifest.get("items")
    if isinstance(direct_items, list):
        for item in direct_items:
            if isinstance(item, dict):
                rows.append(dict(item))
        if rows:
            return rows

    modules = manifest.get("modules")
    if not isinstance(modules, dict):
        return rows

    for module_name, module_items in modules.items():
        if not isinstance(module_items, list):
            continue
        for item in module_items:
            if isinstance(item, str):
                rows.append({"module": module_name, "path": item})
            elif isinstance(item, dict):
                row = dict(item)
                row.setdefault("module", module_name)
                rows.append(row)
    return rows


def _scan_deliverable_files(files_root: Path, *, limit: int) -> List[Dict[str, Any]]:
    if not files_root.exists() or not files_root.is_dir():
        return []

    rows: List[Dict[str, Any]] = []
    for path in sorted(files_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(files_root)
        module = rel.parts[0] if rel.parts else "docs"
        rows.append(
            {
                "module": module,
                "path": str(rel),
                "size": path.stat().st_size,
                "updated_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
                "status": "final",
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _materialize_deliverable_items(
    *,
    manifest: Dict[str, Any],
    files_root: Path,
    include_draft: bool,
    module_filter: Optional[str],
    limit: int,
) -> Tuple[List[DeliverableItem], Dict[str, List[DeliverableItem]]]:
    rows = _manifest_items(manifest)
    if not rows:
        rows = _scan_deliverable_files(files_root, limit=limit)

    normalized_filter = (module_filter or "").strip().lower() or None
    resolved_root = files_root.resolve()

    items: List[DeliverableItem] = []
    modules: Dict[str, List[DeliverableItem]] = {}
    for row in rows:
        raw_path = str(row.get("path") or "").strip()
        if not raw_path:
            continue

        module = str(row.get("module") or "").strip().lower()
        if not module:
            module = Path(raw_path).parts[0] if Path(raw_path).parts else "docs"

        if normalized_filter and module != normalized_filter:
            continue

        status_value = str(row.get("status") or "final").strip().lower() or "final"
        if not include_draft and status_value == "draft":
            continue

        normalized_path = raw_path.lstrip("/").replace("\\", "/")
        target = (files_root / normalized_path).resolve()
        try:
            target.relative_to(resolved_root)
        except ValueError:
            continue

        stat = target.stat() if target.exists() and target.is_file() else None
        extension = Path(normalized_path).suffix.lower().lstrip(".") or None
        size = int(row.get("size") or 0)
        if stat is not None and size <= 0:
            size = stat.st_size
        updated_at = row.get("updated_at")
        if stat is not None and not isinstance(updated_at, str):
            updated_at = datetime.fromtimestamp(stat.st_mtime).isoformat()

        item = DeliverableItem(
            module=module,
            path=normalized_path,
            name=Path(normalized_path).name,
            status=status_value,
            size=max(0, size),
            extension=extension,
            updated_at=updated_at if isinstance(updated_at, str) else None,
            source_path=str(row.get("source_path")) if row.get("source_path") is not None else None,
        )
        items.append(item)
        modules.setdefault(module, []).append(item)

        if len(items) >= limit:
            break

    for module_name in list(modules.keys()):
        modules[module_name] = sorted(modules[module_name], key=lambda entry: entry.path)
    items.sort(key=lambda entry: (entry.module, entry.path))
    return items, modules


def _iter_items(
    base_dir: Path,
    *,
    max_depth: int,
    include_dirs: bool,
    limit: int,
    extensions: Optional[List[str]] = None,
    hidden_prefixes: Optional[List[str]] = None,
    hidden_check_prefix: str = "",
) -> List[ArtifactItem]:
    items: List[ArtifactItem] = []
    base_dir = base_dir.resolve()

    # Hidden prefixes are session-root-relative; when the walk starts from a
    # sub-directory (path_prefix views), re-root the relative paths before
    # comparing or hidden entries would leak into the listing.
    hidden_check_prefix = str(hidden_check_prefix or "").strip().strip("/").replace("\\", "/")

    _SKIP_DIR_NAMES = {
        "deliverables",
        "__pycache__",
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "_scratch",
    }

    _SKIP_DIR_PREFIXES = ("run_",)

    _SKIP_FILE_NAMES = {".source_owners.json", ".DS_Store", "Thumbs.db"}

    def _should_skip_dir(dir_path: Path) -> bool:
        name = dir_path.name
        if name in _SKIP_DIR_NAMES:
            return True
        if any(name.startswith(prefix) for prefix in _SKIP_DIR_PREFIXES):
            return True
        return False

    def _walk(current: Path, depth: int) -> None:
        if len(items) >= limit:
            return
        if max_depth > 0 and depth > max_depth:
            return

        try:
            children = sorted(current.iterdir())
        except PermissionError:
            return

        for path in children:
            if len(items) >= limit:
                return

            try:
                rel_path = path.relative_to(base_dir)
            except ValueError:
                continue
            normalized_rel = str(rel_path).replace("\\", "/")
            hidden_rel = (
                f"{hidden_check_prefix}/{normalized_rel}"
                if hidden_check_prefix
                else normalized_rel
            )
            if _path_is_hidden(hidden_rel, hidden_prefixes or []):
                continue

            if path.is_dir():
                if _should_skip_dir(path):
                    continue
                if include_dirs:
                    items.append(
                        ArtifactItem(
                            name=path.name,
                            path=normalized_rel,
                            type="directory",
                            size=0,
                            modified_at=datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
                        )
                    )
                _walk(path, depth + 1)
                continue

            ext = path.suffix.lower().lstrip(".") if path.suffix else None
            if extensions and ext not in extensions:
                continue
            if path.name in _SKIP_FILE_NAMES:
                continue

            stat = path.stat()
            items.append(
                ArtifactItem(
                    name=path.name,
                    path=normalized_rel,
                    type="file",
                    size=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    extension=ext,
                )
            )

    _walk(base_dir, 0)

    items.sort(key=lambda item: item.modified_at or "", reverse=True)
    return items


def _resolve_reference_deliverable(*, session_dir: Path, rel_path: str) -> Optional[Path]:
    """Resolve a manifest row stored as ``storage=reference`` to its source.

    Reference rows are not copied into deliverables/latest (big-file policy);
    the file is served from its source location, which must live inside the
    session directory.

    Gallery items emitted by the publish pipeline may reference the artifact's
    *source* path (e.g. ``_scratch/<task>/run_<ts>/deliverables/x.png``) rather
    than the published path under ``deliverables/latest``.  When no exact
    reference row matches, fall back to the manifest row whose ``source_path``
    ends with the requested session-relative path and serve its published copy.
    """
    manifest = _safe_json_load(_deliverables_root(session_dir) / "manifest_latest.json")
    normalized = rel_path.replace("\\", "/").strip("/")
    items = manifest.get("items") or []
    resolved = _resolve_reference_row(session_dir=session_dir, items=items, rel_path=normalized)
    if resolved is not None:
        return resolved
    return _resolve_source_path_row(session_dir=session_dir, items=items, rel_path=normalized)


def _resolve_reference_row(*, session_dir: Path, items: Any, rel_path: str) -> Optional[Path]:
    for item in items:
        if not isinstance(item, dict):
            continue
        item_path = str(item.get("path") or "").replace("\\", "/").strip("/")
        if item_path != rel_path:
            continue
        if str(item.get("storage") or "") != "reference":
            continue
        source = str(item.get("reference_source") or "").strip()
        if not source:
            source_rel = str(item.get("source_path") or "").strip()
            if not source_rel:
                return None
            try:
                project_root = session_dir.resolve().parents[1]
            except Exception:
                return None
            source = str(project_root / source_rel)
        try:
            resolved = Path(source).resolve()
        except Exception:
            return None
        if not resolved.is_file():
            return None
        try:
            resolved.relative_to(session_dir.resolve())
        except Exception:
            return None
        return resolved
    return None


def _resolve_source_path_row(*, session_dir: Path, items: Any, rel_path: str) -> Optional[Path]:
    if not rel_path or ".." in rel_path.split("/"):
        return None
    latest_root = _deliverables_latest_dir(session_dir)
    for item in items:
        if not isinstance(item, dict):
            continue
        source_rel = str(item.get("source_path") or "").replace("\\", "/").strip().strip("/")
        if not source_rel:
            continue
        if source_rel != rel_path and not source_rel.endswith("/" + rel_path):
            continue
        item_path = str(item.get("path") or "").replace("\\", "/").strip("/")
        if not item_path or ".." in item_path.split("/"):
            continue
        candidate = (latest_root / item_path).resolve()
        try:
            candidate.relative_to(latest_root.resolve())
        except ValueError:
            continue
        if not candidate.is_file():
            continue
        return candidate
    return None
