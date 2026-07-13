"""Platform-bound project context routes.

Project data belongs to the main platform.  These routes only expose the
server-verified project binding from a platform SSO session; they never trust
browser-supplied platform identity or fall back to Agent-host filesystem paths.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
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


def _project_context_for_request(request: Request, project_id: int) -> dict:
    platform_user_id, trusted_project_id = require_bound_platform_project(request, project_id)
    return get_platform_api_client().get_project_context(platform_user_id, trusted_project_id)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: int, request: Request) -> ProjectResponse:
    project_data = _project_context_for_request(request, project_id)
    raw_roots = project_data.get("data_roots") or []
    data_roots = [
        DataRoot(
            # Platform file roots are opaque to browsers and Agent-host APIs.
            path="",
            label=str(root.get("label") or root.get("name") or f"Data root {index + 1}"),
            mode=str(root.get("mode") or "readonly"),
        )
        for index, root in enumerate(raw_roots)
        if isinstance(root, dict)
    ]
    raw_provider = project_data.get("model_provider")
    model_provider = None
    if isinstance(raw_provider, dict):
        model_provider = ModelProvider(
            type=str(raw_provider.get("type") or "") or None,
            model=str(raw_provider.get("model") or "") or None,
            base_url=str(raw_provider.get("base_url") or ""),
            model_options=raw_provider.get("model_options")
            if isinstance(raw_provider.get("model_options"), list)
            else None,
        )
    return ProjectResponse(
        data=ProjectData(
            id=int(project_data.get("id") or project_id),
            data_roots=data_roots,
            model_provider=model_provider,
        )
    )


@router.get("/{project_id}/files", response_model=FileTreeResponse)
async def get_project_files(project_id: int, request: Request) -> FileTreeResponse:
    _project_context_for_request(request, project_id)
    get_platform_api_client().require_project_files_capability()
    raise AssertionError("platform capability method must raise")


@router.post("/{project_id}/select-files", response_model=SelectedFilesResponse)
async def select_project_files(
    project_id: int,
    request: Request,
    payload: SelectedFilesRequest,
) -> SelectedFilesResponse:
    if payload.project_id != project_id:
        raise HTTPException(status_code=400, detail="Project path and payload must match")
    _project_context_for_request(request, project_id)
    get_platform_api_client().require_project_file_selection_capability()
    raise AssertionError("platform capability method must raise")
