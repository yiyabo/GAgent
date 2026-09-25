"""Artifact routes package (compatibility facade).

This package is the split-out form of the former
``app/routers/artifact_routes.py`` module. The import path
``app.routers.artifact_routes`` is unchanged: it is the registration contract
used by ``app/routers/__init__.py``.

Package layout:

- ``schemas.py``   Pydantic DTOs (pure data)

The HTTP endpoints, the router and registration stay in this facade, and every
original module-level name is re-exported here (private names included), so
``from app.routers.artifact_routes import X`` and ``artifact_routes.X`` access
keep working unchanged. Sibling modules must not import facade names at import
time: the names tests patch (``RUNTIME_DIR``, ``INFO_SESSIONS_DIR``,
``MAMMOTH_AVAILABLE``, ``mammoth``, ``get_deliverable_settings``,
``_ensure_session_access``, ``_load_hidden_artifact_prefixes``,
``_resolve_session_dir``, ``_workspace_root``, ...) are read through the facade
at call time (``from .. import artifact_routes as facade``).
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Literal, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, StreamingResponse

from app.database import get_db
from app.config.deliverable_config import get_deliverable_settings
from app.services.request_principal import ensure_owner_access
from app.services.session_paths import normalize_session_base

from .. import register_router
from ..chat.artifact_gallery import is_image_artifact_path
from ..chat.subject_identity import _workspace_root
from .schemas import (
    ArtifactItem,
    ArtifactListResponse,
    ArtifactRenderResponse,
    ArtifactTextResponse,
    BatchDownloadFileEntry,
    BatchDownloadRequest,
    DeliverableItem,
    DeliverableListResponse,
    DeliverableManifestResponse,
    DeliverableVersionSummary,
)
from .rendering import (
    _get_render_cache_path,
    _iter_render_dependency_files,
    _render_cache_dir,
    _render_docx_to_html,
    _render_latex_to_pdf,
    _render_markdown_to_html,
    _rewrite_markdown_image_urls,
)
from .session_dirs import (
    _assert_path_within,
    _candidate_score,
    _deliverables_history_dir,
    _deliverables_latest_dir,
    _deliverables_root,
    _ensure_session_access,
    _find_session_candidates,
    _info_sessions_root_dir,
    _resolve_session_dir,
    _runtime_root_dir,
    _strip_session_prefixes,
)

# Optional markdown import
try:
    import markdown
    MARKDOWN_AVAILABLE = True
except ImportError:
    MARKDOWN_AVAILABLE = False

try:
    import mammoth
    MAMMOTH_AVAILABLE = True
except ImportError:
    MAMMOTH_AVAILABLE = False

# ``__file__`` now lives one directory deeper (package instead of module), so
# the project root is four ``parent`` hops up: the resolved values are still
# <project>/runtime and <project>/data/information_sessions.
RUNTIME_DIR = Path(__file__).parent.parent.parent.parent.resolve() / "runtime"
INFO_SESSIONS_DIR = Path(__file__).parent.parent.parent.parent.resolve() / "data" / "information_sessions"

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


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
    if get_deliverable_settings().single_version_only:
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
    settings = get_deliverable_settings()
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
        session_dir = _resolve_session_dir(session_id, purpose="deliverables")
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


@router.get("/sessions/{session_id}", response_model=ArtifactListResponse)
async def list_session_artifacts(
    session_id: str,
    request: Request,
    max_depth: int = Query(4, ge=1, le=10),
    include_dirs: bool = Query(False),
    limit: int = Query(500, ge=1, le=5000),
    extensions: Optional[str] = Query(None),
    path_prefix: Optional[str] = Query(None),
) -> ArtifactListResponse:
    _ensure_session_access(session_id, request)
    session_dir = _resolve_session_dir(session_id, purpose="raw")
    if not session_dir.exists():
        # Sessions whose runtime files were never migrated (or already cleaned
        # up) must return an empty listing instead of a 500 FileNotFoundError.
        return ArtifactListResponse(
            session_id=session_id,
            root_path=str(session_dir),
            items=[],
            count=0,
        )
    hidden_prefixes = _load_hidden_artifact_prefixes(session_id)
    ext_list = None
    if extensions:
        ext_list = [ext.strip().lower().lstrip(".") for ext in extensions.split(",") if ext.strip()]

    base_dir = session_dir
    normalized_prefix = str(path_prefix or "").strip().strip("/").replace("\\", "/")
    if normalized_prefix:
        requested_base = (session_dir / normalized_prefix).resolve()
        _assert_path_within(requested_base, session_dir, detail="Invalid artifact path prefix")
        if not requested_base.exists() or not requested_base.is_dir():
            return ArtifactListResponse(
                session_id=session_id,
                root_path=str(requested_base),
                items=[],
                count=0,
            )
        base_dir = requested_base

    items = _iter_items(
        base_dir,
        max_depth=max_depth,
        include_dirs=include_dirs,
        limit=limit,
        extensions=ext_list,
        hidden_prefixes=hidden_prefixes,
        hidden_check_prefix=normalized_prefix,
    )

    if normalized_prefix:
        prefixed_items: List[ArtifactItem] = []
        for item in items:
            rel_path = str(item.path or "").strip().strip("/")
            item_path = normalized_prefix if not rel_path else f"{normalized_prefix}/{rel_path}"
            prefixed_items.append(
                item.model_copy(update={"path": item_path})
            )
        items = prefixed_items

    # The "raw_files" view is the user-facing workspace listing, but agents
    # also write final outputs to the session-level results/ tree.  Union it
    # in so those files are visible (and downloadable) from the same panel.
    if normalized_prefix == "raw_files":
        results_dir = (session_dir / "results").resolve()
        if results_dir.is_dir():
            for item in _iter_items(
                results_dir,
                max_depth=max_depth,
                include_dirs=include_dirs,
                limit=limit,
                extensions=ext_list,
                hidden_prefixes=hidden_prefixes,
                hidden_check_prefix="results",
            ):
                rel_path = str(item.path or "").strip().strip("/")
                item_path = "results" if not rel_path else f"results/{rel_path}"
                items.append(item.model_copy(update={"path": item_path}))
            items = items[:limit]

    return ArtifactListResponse(
        session_id=session_id,
        root_path=str(base_dir),
        items=items,
        count=len(items),
    )


@router.get("/sessions/{session_id}/file")
async def get_session_artifact_file(
    session_id: str,
    request: Request,
    path: str = Query(..., min_length=1),
) -> FileResponse:
    _ensure_session_access(session_id, request)
    session_dir = _resolve_session_dir(session_id, purpose="raw")
    target = (session_dir / path).resolve()

    _assert_path_within(target, session_dir, detail="Invalid artifact path")
    hidden_prefixes = _load_hidden_artifact_prefixes(session_id)
    rel_path = str(target.relative_to(session_dir)).replace("\\", "/")
    if _path_is_hidden(rel_path, hidden_prefixes):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")

    media_type, _ = mimetypes.guess_type(str(target))
    ext = target.suffix.lower()
    is_inline_type = ext in (".pdf", ".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif", ".html", ".txt", ".json", ".md")
    return FileResponse(
        path=target,
        media_type=media_type or "application/octet-stream",
        filename=target.name if not is_inline_type else None,
        content_disposition_type="inline" if is_inline_type else "attachment",
    )


@router.get("/sessions/{session_id}/workspace-file")
async def get_session_workspace_file(
    session_id: str,
    request: Request,
    path: str = Query(..., min_length=1),
) -> FileResponse:
    _ensure_session_access(session_id, request)
    workspace_root = _workspace_root().resolve()
    raw_path = str(path or "").strip()
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = workspace_root / candidate
    target = candidate.resolve(strict=False)

    _assert_path_within(target, workspace_root, detail="Invalid workspace path")
    if not is_image_artifact_path(target.name):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace file not found",
        )
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace file not found")

    media_type, _ = mimetypes.guess_type(str(target))
    ext = target.suffix.lower()
    is_inline_type = ext in (".pdf", ".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif", ".html", ".txt", ".json", ".md")
    return FileResponse(
        path=target,
        media_type=media_type or "application/octet-stream",
        filename=target.name if not is_inline_type else None,
        content_disposition_type="inline" if is_inline_type else "attachment",
    )


@router.get("/sessions/{session_id}/text", response_model=ArtifactTextResponse)
async def get_session_artifact_text(
    session_id: str,
    request: Request,
    path: str = Query(..., min_length=1),
    max_bytes: int = Query(200000, ge=1024, le=2_000_000),
) -> ArtifactTextResponse:
    _ensure_session_access(session_id, request)
    session_dir = _resolve_session_dir(session_id, purpose="raw")
    target = (session_dir / path).resolve()

    _assert_path_within(target, session_dir, detail="Invalid artifact path")
    hidden_prefixes = _load_hidden_artifact_prefixes(session_id)
    rel_path = str(target.relative_to(session_dir)).replace("\\", "/")
    if _path_is_hidden(rel_path, hidden_prefixes):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")

    raw = target.read_bytes()
    truncated = len(raw) > max_bytes
    if truncated:
        raw = raw[:max_bytes]
    content = raw.decode("utf-8", errors="replace")
    return ArtifactTextResponse(path=path, content=content, truncated=truncated)


@router.get("/sessions/{session_id}/deliverables", response_model=DeliverableListResponse)
async def list_session_deliverables(
    session_id: str,
    request: Request,
    scope: Literal["latest", "history"] = Query("latest"),
    version: Optional[str] = Query(None),
    include_draft: bool = Query(False),
    module: Optional[str] = Query(None),
    limit: int = Query(1000, ge=1, le=5000),
) -> DeliverableListResponse:
    _ensure_session_access(session_id, request)
    try:
        session_dir = _resolve_session_dir(session_id, purpose="deliverables")
    except HTTPException as exc:
        if exc.status_code != status.HTTP_404_NOT_FOUND:
            raise
        return DeliverableListResponse(
            session_id=session_id,
            scope="latest",
            version_id=None,
            root_path="",
            modules={},
            items=[],
            count=0,
            paper_status={
                "completed_sections": [],
                "missing_sections": [],
                "total_sections": 0,
                "completed_count": 0,
            },
            available_versions=[],
        )
    resolved_scope, resolved_version, files_root, _manifest_path, manifest = _resolve_deliverable_view(
        session_dir=session_dir,
        scope=scope,
        version=version,
    )

    items, modules = _materialize_deliverable_items(
        manifest=manifest,
        files_root=files_root,
        include_draft=include_draft,
        module_filter=module,
        limit=limit,
    )
    paper_status = _paper_status_from_manifest(manifest)
    release_meta = _release_meta_from_manifest(manifest)
    if (
        not include_draft
        and release_meta["release_state"] != "blocked"
        and not items
        and int(paper_status.get("completed_count") or 0) > 0
    ):
        items, modules = _materialize_deliverable_items(
            manifest=manifest,
            files_root=files_root,
            include_draft=True,
            module_filter=module,
            limit=limit,
        )
    versions = _list_deliverable_versions(history_root=_deliverables_history_dir(session_dir))

    return DeliverableListResponse(
        session_id=session_id,
        scope=resolved_scope,
        version_id=resolved_version,
        root_path=str(files_root),
        modules=modules,
        items=items,
        count=len(items),
        paper_status=paper_status,
        release_state=release_meta["release_state"],
        public_release_ready=release_meta["public_release_ready"],
        release_summary=release_meta["release_summary"],
        hidden_artifact_prefixes=release_meta["hidden_artifact_prefixes"],
        available_versions=versions,
    )


@router.get("/sessions/{session_id}/deliverables/manifest", response_model=DeliverableManifestResponse)
async def get_session_deliverables_manifest(
    session_id: str,
    request: Request,
    scope: Literal["latest", "history"] = Query("latest"),
    version: Optional[str] = Query(None),
) -> DeliverableManifestResponse:
    _ensure_session_access(session_id, request)
    try:
        session_dir = _resolve_session_dir(session_id, purpose="deliverables")
    except HTTPException as exc:
        if exc.status_code != status.HTTP_404_NOT_FOUND:
            raise
        return DeliverableManifestResponse(
            session_id=session_id,
            scope="latest",
            version_id=None,
            manifest_path=None,
            manifest={},
            available_versions=[],
        )
    resolved_scope, resolved_version, _files_root, manifest_path, manifest = _resolve_deliverable_view(
        session_dir=session_dir,
        scope=scope,
        version=version,
    )

    versions = _list_deliverable_versions(history_root=_deliverables_history_dir(session_dir))
    release_meta = _release_meta_from_manifest(manifest)
    return DeliverableManifestResponse(
        session_id=session_id,
        scope=resolved_scope,
        version_id=resolved_version,
        manifest_path=str(manifest_path) if manifest_path.exists() else None,
        manifest=manifest,
        release_state=release_meta["release_state"],
        public_release_ready=release_meta["public_release_ready"],
        release_summary=release_meta["release_summary"],
        hidden_artifact_prefixes=release_meta["hidden_artifact_prefixes"],
        available_versions=versions,
    )


@router.get("/sessions/{session_id}/deliverables/file")
async def get_session_deliverable_file(
    session_id: str,
    request: Request,
    path: str = Query(..., min_length=1),
    version: Optional[str] = Query(None),
) -> FileResponse:
    _ensure_session_access(session_id, request)
    session_dir = _resolve_session_dir(session_id, purpose="deliverables")
    _, _, files_root, _, _ = _resolve_deliverable_view(
        session_dir=session_dir,
        scope="history" if version else "latest",
        version=version,
    )
    target = (files_root / path).resolve()

    _assert_path_within(target, files_root.resolve(), detail="Invalid deliverable path")
    if not target.exists() or not target.is_file():
        reference_target = _resolve_reference_deliverable(session_dir=session_dir, rel_path=path)
        if reference_target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deliverable file not found")
        target = reference_target

    media_type, _ = mimetypes.guess_type(str(target))
    ext = target.suffix.lower()
    is_inline_type = ext in (".pdf", ".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif", ".html", ".txt", ".json", ".md")
    return FileResponse(
        path=target,
        media_type=media_type or "application/octet-stream",
        filename=target.name if not is_inline_type else None,
        content_disposition_type="inline" if is_inline_type else "attachment",
    )


@router.get("/sessions/{session_id}/deliverables/text", response_model=ArtifactTextResponse)
async def get_session_deliverable_text(
    session_id: str,
    request: Request,
    path: str = Query(..., min_length=1),
    version: Optional[str] = Query(None),
    max_bytes: int = Query(200000, ge=1024, le=2_000_000),
) -> ArtifactTextResponse:
    _ensure_session_access(session_id, request)
    session_dir = _resolve_session_dir(session_id, purpose="deliverables")
    _, _, files_root, _, _ = _resolve_deliverable_view(
        session_dir=session_dir,
        scope="history" if version else "latest",
        version=version,
    )
    target = (files_root / path).resolve()

    _assert_path_within(target, files_root.resolve(), detail="Invalid deliverable path")
    if not target.exists() or not target.is_file():
        reference_target = _resolve_reference_deliverable(session_dir=session_dir, rel_path=path)
        if reference_target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deliverable not found")
        target = reference_target

    raw = target.read_bytes()
    truncated = len(raw) > max_bytes
    if truncated:
        raw = raw[:max_bytes]
    content = raw.decode("utf-8", errors="replace")
    return ArtifactTextResponse(path=path, content=content, truncated=truncated)


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


@router.get("/sessions/{session_id}/artifacts/registry")
async def get_session_artifact_registry(session_id: str, request: Request) -> Dict[str, Any]:
    """Per-session artifact registry snapshot (event-stream fact source)."""
    _ensure_session_access(session_id, request)
    session_dir = _resolve_session_dir(session_id, purpose="generic")
    payload = _safe_json_load(session_dir / "artifacts" / "registry.json")
    if not payload:
        return {
            "schema_version": 1,
            "session_id": session_id,
            "updated_at": None,
            "event_ids": [],
            "items": {},
        }
    return payload


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
                raw_session_dir = _resolve_session_dir(session_id, purpose="raw")
            if hidden_prefixes is None:
                hidden_prefixes = _load_hidden_artifact_prefixes(session_id)

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
                deliverable_session_dir = _resolve_session_dir(session_id, purpose="deliverables")
            if hidden_prefixes is None:
                hidden_prefixes = _load_hidden_artifact_prefixes(session_id)

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


@router.post("/sessions/{session_id}/batch-download")
async def batch_download_session_artifacts(
    session_id: str,
    request: Request,
    body: BatchDownloadRequest,
) -> StreamingResponse:
    _ensure_session_access(session_id, request)

    if not body.files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files requested",
        )
    if len(body.files) > 500:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Too many files (max 500)",
        )

    collected = _collect_batch_files(session_id, body.files)

    tmp = tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for resolved_path, arcname in collected:
                zf.write(resolved_path, arcname)
        tmp.seek(0)
    except Exception:
        tmp.close()
        raise

    def _iter_chunks() -> Iterator[bytes]:
        try:
            while True:
                chunk = tmp.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            tmp.close()

    filename = f"artifacts-{session_id}-{datetime.now().strftime('%Y%m%dT%H%M%S')}.zip"
    return StreamingResponse(
        _iter_chunks(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/sessions/{session_id}/render", response_model=ArtifactRenderResponse)
async def render_artifact(
    session_id: str,
    request: Request,
    path: str = Query(..., min_length=1, description="Path to the file to render"),
    source_type: Literal["raw", "deliverables"] = Query("raw", description="Source type"),
    version: Optional[str] = Query(None, description="Version for deliverables"),
) -> ArtifactRenderResponse:
    """
    Render a document to preview format:
    - .tex files -> PDF (via LaTeX compilation)
    - .md files -> HTML (via Markdown rendering)
    
    Rendered files are cached for performance.
    """
    _ensure_session_access(session_id, request)
    # Resolve file path
    if source_type == "deliverables":
        session_dir = _resolve_session_dir(session_id, purpose="deliverables")
        _, _, files_root, _, _ = _resolve_deliverable_view(
            session_dir=session_dir,
            scope="history" if version else "latest",
            version=version,
        )
        target = (files_root / path).resolve()
        root_dir = files_root.resolve()
    else:
        session_dir = _resolve_session_dir(session_id, purpose="raw")
        target = (session_dir / path).resolve()
        root_dir = session_dir.resolve()

    _assert_path_within(target, root_dir, detail="Invalid file path")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    extension = target.suffix.lower().lstrip(".")

    # Initialize render cache directory
    render_cache_dir = _render_cache_dir()
    render_cache_dir.mkdir(parents=True, exist_ok=True)

    if extension == "tex":
        # LaTeX -> PDF
        cache_path = _get_render_cache_path(target, "pdf")
        cached = cache_path.exists()

        if not cached:
            success = _render_latex_to_pdf(target, cache_path)
            if not success:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to compile LaTeX document. Ensure the document is valid and LaTeX is installed."
                )

        # Return URL to the cached PDF
        return ArtifactRenderResponse(
            path=path,
            format="pdf",
            url=f"/artifacts/rendered/{cache_path.name}",
            rendered_at=datetime.fromtimestamp(cache_path.stat().st_mtime).isoformat(),
            cached=cached,
        )

    elif extension == "md":
        # Markdown -> HTML
        context_hash = f"{session_id}:{source_type}:{str(target.relative_to(root_dir))}"
        cache_path = _get_render_cache_path(target, "html", extra_hash=context_hash)
        cached = cache_path.exists()

        if not cached:
            content = target.read_text(encoding="utf-8")
            base_dir = str(target.relative_to(root_dir).parent) if target.parent != root_dir else ""
            content = _rewrite_markdown_image_urls(
                content, session_id=session_id, source_type=source_type, base_dir=base_dir
            )
            html = _render_markdown_to_html(content)
            cache_path.write_text(html, encoding="utf-8")

        return ArtifactRenderResponse(
            path=path,
            format="html",
            content=cache_path.read_text(encoding="utf-8"),
            rendered_at=datetime.fromtimestamp(cache_path.stat().st_mtime).isoformat(),
            cached=cached,
        )

    elif extension == "docx":
        if not MAMMOTH_AVAILABLE:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="mammoth is not installed. Run: pip install mammoth",
            )
        context_hash = f"{session_id}:{source_type}:{str(target.relative_to(root_dir))}"
        cache_path = _get_render_cache_path(target, "html", extra_hash=context_hash)
        cached = cache_path.exists()
        if not cached:
            html = _render_docx_to_html(target)
            cache_path.write_text(html, encoding="utf-8")
        return ArtifactRenderResponse(
            path=path,
            format="html",
            content=cache_path.read_text(encoding="utf-8"),
            rendered_at=datetime.fromtimestamp(cache_path.stat().st_mtime).isoformat(),
            cached=cached,
        )

    else:
        # Return raw text for other files
        content = target.read_text(encoding="utf-8", errors="replace")[:200000]
        return ArtifactRenderResponse(
            path=path,
            format="text",
            content=content,
            rendered_at=datetime.now().isoformat(),
            cached=False,
        )


@router.get("/rendered/{filename}")
async def get_rendered_file(filename: str) -> FileResponse:
    """Serve a cached rendered file (PDF from LaTeX compilation)."""
    # Sanitize filename to prevent directory traversal
    safe_filename = Path(filename).name
    file_path = _render_cache_dir() / safe_filename

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rendered file not found")

    media_type, _ = mimetypes.guess_type(str(file_path))
    return FileResponse(
        path=file_path,
        media_type=media_type or "application/octet-stream",
        filename=safe_filename,
    )


register_router(
    namespace="artifacts",
    version="v1",
    path="/artifacts",
    router=router,
    tags=["artifacts"],
    description="Runtime artifact listing and preview APIs",
)
