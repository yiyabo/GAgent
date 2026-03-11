"""Runtime artifact listing and preview routes."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config.deliverable_config import get_deliverable_settings
from app.services.session_paths import normalize_session_base

from . import register_router

# Optional markdown import
try:
    import markdown
    MARKDOWN_AVAILABLE = True
except ImportError:
    MARKDOWN_AVAILABLE = False

RUNTIME_DIR = Path(__file__).parent.parent.parent.resolve() / "runtime"
INFO_SESSIONS_DIR = Path(__file__).parent.parent.parent.resolve() / "data" / "information_sessions"

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


class ArtifactItem(BaseModel):
    name: str
    path: str
    type: Literal["file", "directory"]
    size: int = 0
    modified_at: Optional[str] = None
    extension: Optional[str] = None


class ArtifactListResponse(BaseModel):
    session_id: str
    root_path: str
    items: List[ArtifactItem]
    count: int


class ArtifactTextResponse(BaseModel):
    path: str
    content: str
    truncated: bool = False


class DeliverableItem(BaseModel):
    module: str
    path: str
    name: str
    status: str = "final"
    size: int = 0
    extension: Optional[str] = None
    updated_at: Optional[str] = None
    source_path: Optional[str] = None


class DeliverableVersionSummary(BaseModel):
    version_id: str
    created_at: Optional[str] = None
    published_files_count: int = 0
    published_modules: List[str] = Field(default_factory=list)


class DeliverableListResponse(BaseModel):
    session_id: str
    scope: Literal["latest", "history"] = "latest"
    version_id: Optional[str] = None
    root_path: str
    modules: Dict[str, List[DeliverableItem]] = Field(default_factory=dict)
    items: List[DeliverableItem] = Field(default_factory=list)
    count: int = 0
    paper_status: Dict[str, Any] = Field(default_factory=dict)
    release_state: str = "final"
    public_release_ready: bool = True
    release_summary: Optional[str] = None
    hidden_artifact_prefixes: List[str] = Field(default_factory=list)
    available_versions: List[DeliverableVersionSummary] = Field(default_factory=list)


class DeliverableManifestResponse(BaseModel):
    session_id: str
    scope: Literal["latest", "history"] = "latest"
    version_id: Optional[str] = None
    manifest_path: Optional[str] = None
    manifest: Dict[str, Any] = Field(default_factory=dict)
    release_state: str = "final"
    public_release_ready: bool = True
    release_summary: Optional[str] = None
    hidden_artifact_prefixes: List[str] = Field(default_factory=list)
    available_versions: List[DeliverableVersionSummary] = Field(default_factory=list)


class ArtifactRenderResponse(BaseModel):
    path: str
    format: Literal["pdf", "html", "text"]
    url: Optional[str] = None
    content: Optional[str] = None
    rendered_at: str
    cached: bool = False


def _strip_session_prefixes(value: str) -> str:
    return normalize_session_base(value)


def _find_session_candidates(root: Path, *, session_base: str) -> List[Path]:
    resolved_root = root.resolve()
    if not resolved_root.exists() or not resolved_root.is_dir():
        return []

    candidates: List[Path] = []
    for item in resolved_root.iterdir():
        if not item.is_dir():
            continue
        try:
            candidate = item.resolve()
        except Exception:
            continue
        if not str(candidate).startswith(str(resolved_root)):
            continue
        if _strip_session_prefixes(item.name) != session_base:
            continue
        candidates.append(candidate)
    return candidates


def _candidate_score(candidate: Path, *, purpose: str, source: str) -> Tuple[int, float]:
    score = 0
    deliverables_root = candidate / "deliverables"
    deliverables_manifest = deliverables_root / "manifest_latest.json"
    deliverables_latest = deliverables_root / "latest"
    tool_outputs = candidate / "tool_outputs"

    if purpose == "raw":
        if tool_outputs.exists():
            score += 100
        # New writes are runtime-first; keep info root as legacy fallback.
        if source == "runtime":
            score += 20
        elif source == "info":
            score += 5
        if deliverables_manifest.exists() or deliverables_latest.exists():
            score += 5
    elif purpose == "deliverables":
        if deliverables_manifest.exists():
            score += 120
        elif deliverables_latest.exists():
            score += 90
        elif deliverables_root.exists():
            score += 60
        if source == "runtime":
            score += 15
    else:
        if deliverables_root.exists():
            score += 40
        if tool_outputs.exists():
            score += 40

    try:
        modified = candidate.stat().st_mtime
    except Exception:
        modified = 0.0
    return score, modified


def _resolve_session_dir(
    session_id: str,
    *,
    purpose: Literal["raw", "deliverables", "generic"] = "generic",
) -> Path:
    normalized = session_id.strip()
    if not normalized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="session_id is required")

    session_base = _strip_session_prefixes(normalized)
    if not session_base:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="session_id is invalid")

    runtime_root = RUNTIME_DIR.resolve()
    info_root = INFO_SESSIONS_DIR.resolve()
    runtime_candidates = _find_session_candidates(runtime_root, session_base=session_base)
    info_candidates = _find_session_candidates(info_root, session_base=session_base)

    combined: List[Tuple[Path, str]] = []
    combined.extend((item, "runtime") for item in runtime_candidates)
    combined.extend((item, "info") for item in info_candidates)
    if not combined:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session artifacts not found")

    ranked = sorted(
        combined,
        key=lambda item: _candidate_score(item[0], purpose=purpose, source=item[1]),
        reverse=True,
    )
    return ranked[0][0]


def _deliverables_root(session_dir: Path) -> Path:
    return session_dir / "deliverables"


def _deliverables_latest_dir(session_dir: Path) -> Path:
    return _deliverables_root(session_dir) / "latest"


def _deliverables_history_dir(session_dir: Path) -> Path:
    return _deliverables_root(session_dir) / "history"


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
        if not str(target).startswith(str(resolved_root)):
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
) -> List[ArtifactItem]:
    items: List[ArtifactItem] = []
    base_dir = base_dir.resolve()

    for path in base_dir.rglob("*"):
        try:
            rel_path = path.relative_to(base_dir)
        except ValueError:
            continue
        normalized_rel = str(rel_path).replace("\\", "/")
        if _path_is_hidden(normalized_rel, hidden_prefixes or []):
            continue

        if max_depth > 0 and len(rel_path.parts) > max_depth:
            continue

        if path.is_dir():
            if not include_dirs:
                continue
            items.append(
                ArtifactItem(
                    name=path.name,
                    path=normalized_rel,
                    type="directory",
                    size=0,
                    modified_at=datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
                )
            )
            continue

        ext = path.suffix.lower().lstrip(".") if path.suffix else None
        if extensions and ext not in extensions:
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

        if len(items) >= limit:
            break

    items.sort(key=lambda item: item.modified_at or "", reverse=True)
    return items


@router.get("/sessions/{session_id}", response_model=ArtifactListResponse)
async def list_session_artifacts(
    session_id: str,
    max_depth: int = Query(4, ge=1, le=10),
    include_dirs: bool = Query(False),
    limit: int = Query(500, ge=1, le=5000),
    extensions: Optional[str] = Query(None),
) -> ArtifactListResponse:
    session_dir = _resolve_session_dir(session_id, purpose="raw")
    hidden_prefixes = _load_hidden_artifact_prefixes(session_id)
    ext_list = None
    if extensions:
        ext_list = [ext.strip().lower().lstrip(".") for ext in extensions.split(",") if ext.strip()]

    items = _iter_items(
        session_dir,
        max_depth=max_depth,
        include_dirs=include_dirs,
        limit=limit,
        extensions=ext_list,
        hidden_prefixes=hidden_prefixes,
    )

    return ArtifactListResponse(
        session_id=session_id,
        root_path=str(session_dir),
        items=items,
        count=len(items),
    )


@router.get("/sessions/{session_id}/file")
async def get_session_artifact_file(
    session_id: str,
    path: str = Query(..., min_length=1),
) -> FileResponse:
    session_dir = _resolve_session_dir(session_id, purpose="raw")
    target = (session_dir / path).resolve()

    if not str(target).startswith(str(session_dir)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid artifact path")
    hidden_prefixes = _load_hidden_artifact_prefixes(session_id)
    rel_path = str(target.relative_to(session_dir)).replace("\\", "/")
    if _path_is_hidden(rel_path, hidden_prefixes):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")

    media_type, _ = mimetypes.guess_type(str(target))
    return FileResponse(
        path=target,
        media_type=media_type or "application/octet-stream",
        filename=target.name,
    )


@router.get("/sessions/{session_id}/text", response_model=ArtifactTextResponse)
async def get_session_artifact_text(
    session_id: str,
    path: str = Query(..., min_length=1),
    max_bytes: int = Query(200000, ge=1024, le=2_000_000),
) -> ArtifactTextResponse:
    session_dir = _resolve_session_dir(session_id, purpose="raw")
    target = (session_dir / path).resolve()

    if not str(target).startswith(str(session_dir)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid artifact path")
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
    scope: Literal["latest", "history"] = Query("latest"),
    version: Optional[str] = Query(None),
    include_draft: bool = Query(False),
    module: Optional[str] = Query(None),
    limit: int = Query(1000, ge=1, le=5000),
) -> DeliverableListResponse:
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
    scope: Literal["latest", "history"] = Query("latest"),
    version: Optional[str] = Query(None),
) -> DeliverableManifestResponse:
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
    path: str = Query(..., min_length=1),
    version: Optional[str] = Query(None),
) -> FileResponse:
    session_dir = _resolve_session_dir(session_id, purpose="deliverables")
    _, _, files_root, _, _ = _resolve_deliverable_view(
        session_dir=session_dir,
        scope="history" if version else "latest",
        version=version,
    )
    target = (files_root / path).resolve()

    if not str(target).startswith(str(files_root.resolve())):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid deliverable path")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deliverable file not found")

    media_type, _ = mimetypes.guess_type(str(target))
    return FileResponse(
        path=target,
        media_type=media_type or "application/octet-stream",
        filename=target.name,
    )


@router.get("/sessions/{session_id}/deliverables/text", response_model=ArtifactTextResponse)
async def get_session_deliverable_text(
    session_id: str,
    path: str = Query(..., min_length=1),
    version: Optional[str] = Query(None),
    max_bytes: int = Query(200000, ge=1024, le=2_000_000),
) -> ArtifactTextResponse:
    session_dir = _resolve_session_dir(session_id, purpose="deliverables")
    _, _, files_root, _, _ = _resolve_deliverable_view(
        session_dir=session_dir,
        scope="history" if version else "latest",
        version=version,
    )
    target = (files_root / path).resolve()

    if not str(target).startswith(str(files_root.resolve())):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid deliverable path")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deliverable not found")

    raw = target.read_bytes()
    truncated = len(raw) > max_bytes
    if truncated:
        raw = raw[:max_bytes]
    content = raw.decode("utf-8", errors="replace")
    return ArtifactTextResponse(path=path, content=content, truncated=truncated)


# ----- Document Rendering (LaTeX -> PDF, Markdown -> HTML) -----

# Cache directory for rendered files
RENDER_CACHE_DIR = Path(__file__).parent.parent.parent.resolve() / "runtime" / ".render_cache"


def _iter_render_dependency_files(source_path: Path) -> List[Tuple[str, Path, Path]]:
    src_dir = source_path.parent
    roots: List[Tuple[str, Path]] = [("paper", src_dir)]
    refs_dir = src_dir.parent / "refs"
    if refs_dir.is_dir():
        roots.append(("refs", refs_dir))

    files: List[Tuple[str, Path, Path]] = []
    for label, root in roots:
        for child in sorted(root.rglob("*")):
            if child.is_file():
                files.append((label, root, child))
    return files


def _get_render_cache_path(file_path: Path, extension: str) -> Path:
    """Get cache path for rendered file based on source file hash.

    For .tex files, also incorporates the paper tree plus sibling refs/
    files so that section edits, staged figure updates, and bibliography
    changes all invalidate the cached PDF.
    """
    file_stat = file_path.stat()
    hash_parts = [f"{file_path.absolute()}:{file_stat.st_size}:{file_stat.st_mtime}"]
    if file_path.suffix.lower() == ".tex":
        for label, root, child in _iter_render_dependency_files(file_path):
            try:
                child_stat = child.stat()
                hash_parts.append(
                    f"{label}:{child.relative_to(root)}:{child_stat.st_size}:{child_stat.st_mtime}"
                )
            except OSError:
                continue
    file_hash = hashlib.md5(":".join(hash_parts).encode()).hexdigest()[:16]
    cache_name = f"{file_path.stem}_{file_hash}.{extension}"
    return RENDER_CACHE_DIR / cache_name


def _render_markdown_to_html(content: str) -> str:
    """Render Markdown content to HTML."""
    if MARKDOWN_AVAILABLE:
        md = markdown.Markdown(extensions=['tables', 'fenced_code', 'toc'])
        html_body = md.convert(content)
    else:
        # Fallback: basic HTML conversion
        html_body = (
            content
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('\n\n', '</p><p>')
            .replace('\n', '<br>')
        )
        html_body = f'<p>{html_body}</p>'

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.6;
            max-width: 900px;
            margin: 40px auto;
            padding: 0 20px;
            color: #333;
        }}
        pre {{
            background: #f5f5f5;
            padding: 16px;
            border-radius: 6px;
            overflow-x: auto;
        }}
        code {{
            font-family: 'SF Mono', Monaco, monospace;
            font-size: 0.9em;
            background: #f5f5f5;
            padding: 2px 6px;
            border-radius: 3px;
        }}
        pre code {{
            padding: 0;
            background: none;
        }}
        table {{
            border-collapse: collapse;
            width: 100%;
            margin: 16px 0;
        }}
        th, td {{
            border: 1px solid #ddd;
            padding: 8px 12px;
            text-align: left;
        }}
        th {{
            background: #f5f5f5;
            font-weight: 600;
        }}
        img {{
            max-width: 100%;
            height: auto;
        }}
        h1, h2, h3, h4 {{
            color: #1a1a1a;
            margin-top: 24px;
            margin-bottom: 16px;
        }}
        blockquote {{
            border-left: 4px solid #ddd;
            margin: 0;
            padding-left: 16px;
            color: #666;
        }}
    </style>
</head>
<body>
{html_body}
</body>
</html>"""


def _render_latex_to_pdf(source_path: Path, output_path: Path) -> bool:
    """Render LaTeX file to PDF using pdflatex or xelatex."""
    # Try xelatex first (better Unicode support), then pdflatex
    latex_cmds = ['xelatex', 'pdflatex']
    latex_cmd = None

    for cmd in latex_cmds:
        if shutil.which(cmd):
            latex_cmd = cmd
            break

    if not latex_cmd:
        return False

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            src_dir = source_path.parent

            # Copy the entire directory containing the .tex file
            # (includes sections/ subdirectory for paper projects)
            work_dir = tmpdir_path / src_dir.name
            shutil.copytree(src_dir, work_dir)

            # Copy sibling refs/ if present for bibliography resolution.
            refs_dir = src_dir.parent / "refs"
            if refs_dir.is_dir():
                shutil.copytree(refs_dir, tmpdir_path / "refs")

            temp_tex = work_dir / source_path.name
            tex_stem = temp_tex.stem

            # Compile: latex → bibtex → latex × 2  (full bibliography flow)
            # Step 1: first latex pass
            result = subprocess.run(
                [latex_cmd, '-interaction=nonstopmode', '-halt-on-error', source_path.name],
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                timeout=120,
            )

            # Step 2: run bibtex if .aux exists (needed for \cite → references)
            aux_file = work_dir / f"{tex_stem}.aux"
            bibtex_cmd = shutil.which('bibtex')
            if bibtex_cmd and aux_file.exists():
                subprocess.run(
                    [bibtex_cmd, tex_stem],
                    cwd=str(work_dir),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

            # Steps 3-4: two more latex passes to resolve citations & refs
            for _ in range(2):
                result = subprocess.run(
                    [latex_cmd, '-interaction=nonstopmode', '-halt-on-error', source_path.name],
                    cwd=str(work_dir),
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode != 0:
                    error_match = re.search(r'! (.*?)(?:\n|$)', result.stderr or result.stdout, re.DOTALL)
                    error_msg = error_match.group(1).strip() if error_match else 'LaTeX compilation failed'
                    print(f"LaTeX error: {error_msg}")

            # Move output PDF to cache location
            output_pdf = work_dir / f"{tex_stem}.pdf"
            if output_pdf.exists():
                output_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(output_pdf), str(output_path))
                return True

    except subprocess.TimeoutExpired:
        print("LaTeX compilation timed out")
    except Exception as e:
        print(f"LaTeX compilation error: {e}")

    return False


@router.get("/sessions/{session_id}/render", response_model=ArtifactRenderResponse)
async def render_artifact(
    session_id: str,
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

    if not str(target).startswith(str(root_dir)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid file path")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    extension = target.suffix.lower().lstrip(".")

    # Initialize render cache directory
    RENDER_CACHE_DIR.mkdir(parents=True, exist_ok=True)

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
        cache_path = _get_render_cache_path(target, "html")
        cached = cache_path.exists()

        if not cached:
            content = target.read_text(encoding="utf-8")
            html = _render_markdown_to_html(content)
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
    file_path = RENDER_CACHE_DIR / safe_filename

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rendered file not found")

    return FileResponse(
        path=file_path,
        media_type="application/pdf",
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
