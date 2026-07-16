"""Platform-bound project context and shared-host data_root file access."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.routers import register_router
from app.services.platform_access import require_bound_platform_project
from app.services.platform_api import get_platform_api_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/project", tags=["project"])

register_router(
    namespace="project",
    version="v1",
    path="/project",
    router=router,
    tags=["project"],
    description="Server-bound main-platform project context",
)


class DataRoot(BaseModel):
    path: str = ""
    label: Optional[str] = None
    mode: str = "readonly"


def _normalize_model_options(raw: object) -> Optional[list[str]]:
    if not isinstance(raw, list):
        return None
    options: list[str] = []
    for item in raw:
        if isinstance(item, str):
            text = item.strip()
            if text:
                options.append(text)
            continue
        if isinstance(item, dict):
            for key in ("key", "model", "name", "id", "value"):
                candidate = item.get(key)
                if candidate is None:
                    continue
                text = str(candidate).strip()
                if text:
                    options.append(text)
                    break
    return options or None


class ModelProvider(BaseModel):
    type: Optional[str] = None
    model: Optional[str] = None
    base_url: str = ""
    model_options: Optional[list[str]] = None


class ProjectData(BaseModel):
    id: int
    data_roots: list[DataRoot]
    model_provider: Optional[ModelProvider] = None


class ProjectResponse(BaseModel):
    code: int = 0
    message: str = "success"
    data: Optional[ProjectData] = None


class FileTreeNode(BaseModel):
    key: str
    title: str
    path: str
    is_leaf: bool = False
    children: Optional[list["FileTreeNode"]] = None


class FileTreeResponse(BaseModel):
    code: int = 0
    message: str = "success"
    data: list[FileTreeNode]


class FileReference(BaseModel):
    path: str
    name: str
    data_root_path: str


class SelectedFilesRequest(BaseModel):
    project_id: int
    selected_paths: list[str]
    data_root_index: int = 0
    session_id: Optional[str] = None


class SelectedFilesResponse(BaseModel):
    code: int = 0
    message: str = "success"
    files: list[FileReference]


async def _project_context_for_request(request: Request, project_id: int) -> dict:
    platform_user_id, trusted_project_id = require_bound_platform_project(request, project_id)
    return await get_platform_api_client().get_project_context(platform_user_id, trusted_project_id)


def _raw_data_roots(project_data: dict) -> list[dict]:
    raw = project_data.get("data_roots") or []
    return [root for root in raw if isinstance(root, dict)]


def _resolve_data_root(project_data: dict, data_root_index: int) -> tuple[str, Path]:
    roots = _raw_data_roots(project_data)
    if not roots:
        raise HTTPException(status_code=404, detail="No data roots configured for this project")
    if data_root_index < 0 or data_root_index >= len(roots):
        raise HTTPException(status_code=400, detail="Invalid data_root index")
    root_path = str(roots[data_root_index].get("path") or "").strip()
    if not root_path:
        raise HTTPException(status_code=400, detail="Selected data root has no path")
    root = Path(root_path)
    if not root.exists() or not root.is_dir():
        raise HTTPException(
            status_code=404,
            detail="Data root path is not accessible on the Agent host",
        )
    return root_path, root.resolve()


def _safe_child(root_resolved: Path, relative: str) -> Path:
    rel = str(relative or "").strip().lstrip("/")
    if not rel or rel in {".", ".."} or ".." in Path(rel).parts:
        raise HTTPException(status_code=403, detail="Access denied: invalid path")
    candidate = (root_resolved / rel).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Access denied: path outside data root") from exc
    return candidate


def _build_file_tree(path: Path, root_resolved: Path) -> list[FileTreeNode]:
    nodes: list[FileTreeNode] = []
    try:
        items = sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    except PermissionError:
        logger.warning("Permission denied accessing: %s", path)
        return nodes
    except OSError as exc:
        logger.error("Error reading directory %s: %s", path, exc)
        return nodes

    for item in items:
        if item.name.startswith("."):
            continue
        try:
            relative_path = str(item.resolve().relative_to(root_resolved))
        except ValueError:
            continue
        if item.is_dir():
            children = _build_file_tree(item, root_resolved)
            nodes.append(
                FileTreeNode(
                    key=relative_path,
                    title=item.name,
                    path=relative_path,
                    is_leaf=False,
                    children=children if children else [],
                )
            )
        else:
            nodes.append(
                FileTreeNode(
                    key=relative_path,
                    title=item.name,
                    path=relative_path,
                    is_leaf=True,
                )
            )
    return nodes


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: int, request: Request) -> ProjectResponse:
    project_data = await _project_context_for_request(request, project_id)
    raw_roots = _raw_data_roots(project_data)
    data_roots = [
        DataRoot(
            path="",
            label=str(root.get("label") or root.get("name") or f"Data root {index + 1}"),
            mode=str(root.get("mode") or "readonly"),
        )
        for index, root in enumerate(raw_roots)
    ]
    raw_provider = project_data.get("model_provider")
    model_provider = None
    if isinstance(raw_provider, dict):
        model_provider = ModelProvider(
            type=str(raw_provider.get("type") or "") or None,
            model=str(raw_provider.get("model") or "") or None,
            base_url=str(raw_provider.get("base_url") or ""),
            model_options=_normalize_model_options(raw_provider.get("model_options")),
        )
    return ProjectResponse(
        data=ProjectData(
            id=int(project_data.get("id") or project_id),
            data_roots=data_roots,
            model_provider=model_provider,
        )
    )


@router.get("/{project_id}/files", response_model=FileTreeResponse)
async def get_project_files(
    project_id: int,
    request: Request,
    path: Annotated[Optional[str], Query(description="Relative path within data_root")] = None,
    data_root_index: Annotated[int, Query(description="Index of data_root to browse")] = 0,
) -> FileTreeResponse:
    project_data = await _project_context_for_request(request, project_id)
    try:
        _root_path, root_resolved = _resolve_data_root(project_data, data_root_index)
    except HTTPException as exc:
        if exc.status_code == 404 and "No data roots" in str(exc.detail):
            return FileTreeResponse(code=0, message="success", data=[])
        if exc.status_code == 404 and "not accessible" in str(exc.detail):
            return FileTreeResponse(code=0, message="Data root path not accessible", data=[])
        raise

    target = root_resolved
    if path:
        target = _safe_child(root_resolved, path)
        if not target.exists():
            return FileTreeResponse(code=404, message="Path not found", data=[])
        if not target.is_dir():
            raise HTTPException(status_code=400, detail="Path is not a directory")

    nodes = _build_file_tree(target, root_resolved)
    return FileTreeResponse(code=0, message="success", data=nodes)


@router.post("/{project_id}/select-files", response_model=SelectedFilesResponse)
async def select_project_files(
    project_id: int,
    request: Request,
    payload: SelectedFilesRequest,
) -> SelectedFilesResponse:
    if payload.project_id != project_id:
        raise HTTPException(status_code=400, detail="Project path and payload must match")

    project_data = await _project_context_for_request(request, project_id)
    _root_path, root_resolved = _resolve_data_root(project_data, payload.data_root_index)

    session_upload_dir = None
    if payload.session_id:
        from app.services.session_paths import get_session_upload_dir

        try:
            session_upload_dir = get_session_upload_dir(payload.session_id, create=True)
        except Exception as exc:
            logger.warning("Failed to create session upload dir: %s", exc)

    files: list[FileReference] = []
    for selected_path in payload.selected_paths:
        try:
            source = _safe_child(root_resolved, selected_path)
        except HTTPException:
            logger.warning("Rejected path outside data root: %s", selected_path)
            continue
        if not source.exists() or not source.is_file():
            logger.warning("Invalid or non-existent file path: %s", selected_path)
            continue

        file_ref = FileReference(
            path=str(selected_path).lstrip("/"),
            name=Path(selected_path).name,
            data_root_path="",
        )

        if session_upload_dir is not None:
            try:
                dest_path = session_upload_dir / file_ref.name
                original_dest = dest_path
                counter = 1
                while dest_path.exists():
                    dest_path = original_dest.with_name(
                        f"{original_dest.stem}_{counter}{original_dest.suffix}"
                    )
                    counter += 1
                shutil.copy2(source, dest_path)
                file_ref.path = str(dest_path)
                logger.info("Copied project file to session upload: %s -> %s", source, dest_path)
            except OSError as exc:
                logger.warning("Failed to copy file to session upload, using relative path: %s", exc)

        files.append(file_ref)

    return SelectedFilesResponse(
        code=0,
        message=f"Selected {len(files)} files",
        files=files,
    )
