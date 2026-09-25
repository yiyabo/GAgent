"""Artifact routes package (compatibility facade).

This package is the split-out form of the former
``app/routers/artifact_routes.py`` module. The import path
``app.routers.artifact_routes`` is unchanged: it is the registration contract
used by ``app/routers/__init__.py``.

Package layout:

- ``schemas.py``         Pydantic DTOs (pure data)
- ``session_dirs.py``    session directory discovery, path safety, deliverable roots
- ``deliverable_store.py`` deliverable manifest/version read model, hidden prefixes, item walker
- ``rendering.py``       LaTeX/PDF, docx and Markdown rendering (subprocess + HTML)
- ``batch.py``           batch-download resolution (zip payload collection)

The HTTP endpoints, the router and registration stay in this facade, and every
original module-level name is re-exported here (private names included), so
``from app.routers.artifact_routes import X`` and ``artifact_routes.X`` access
keep working unchanged. Sibling modules must not import facade names at import
time: the names tests patch (``RUNTIME_DIR``, ``INFO_SESSIONS_DIR``,
``MARKDOWN_AVAILABLE``, ``markdown``, ``MAMMOTH_AVAILABLE``, ``mammoth``,
``get_deliverable_settings``, ``_ensure_session_access``,
``_load_hidden_artifact_prefixes``, ``_resolve_session_dir``,
``_workspace_root``) are read through the facade at call time
(``from .. import artifact_routes as facade``).

The two former module-level router->router imports
(``.chat.artifact_gallery.is_image_artifact_path``,
``.chat.subject_identity._workspace_root``, layer-inversion L3) are now lazy
delegates defined below: the import edge happens on first call instead of at
import time, while both names stay resolvable facade attributes so
``monkeypatch.setattr(artifact_routes, "_workspace_root", ...)`` keeps working.
"""

from __future__ import annotations

import mimetypes
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, StreamingResponse

from app.database import get_db
from app.config.deliverable_config import get_deliverable_settings
from app.services.request_principal import ensure_owner_access
from app.services.session_paths import normalize_session_base

from .. import register_router
from .batch import _collect_batch_files
from .deliverable_store import (
    _iter_items,
    _list_deliverable_versions,
    _load_hidden_artifact_prefixes,
    _manifest_items,
    _materialize_deliverable_items,
    _paper_status_from_manifest,
    _path_is_hidden,
    _release_meta_from_manifest,
    _resolve_deliverable_view,
    _resolve_reference_deliverable,
    _resolve_reference_row,
    _resolve_source_path_row,
    _safe_json_load,
    _scan_deliverable_files,
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


# Layer-inversion L3: the two former router->router imports are lazy delegates.
# The import edge now happens on first call, while both names stay module
# attributes so ``monkeypatch.setattr(artifact_routes, "_workspace_root", ...)``
# (test_artifact_routes.py:187/441/464) keeps working.
def is_image_artifact_path(path: Any) -> bool:
    """Lazy re-export of ``chat.artifact_gallery.is_image_artifact_path``."""
    from ..chat.artifact_gallery import is_image_artifact_path as _impl

    return _impl(path)


def _workspace_root() -> Path:
    """Lazy re-export of ``chat.subject_identity._workspace_root``."""
    from ..chat.subject_identity import _workspace_root as _impl

    return _impl()


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
