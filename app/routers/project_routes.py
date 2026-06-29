"""Project routes for managing project context and data roots."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.routers import register_router
from app.services.request_principal import get_request_owner_id
from app.services.sso import get_main_platform_user_id, get_project_context

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/project", tags=["project"])


def _resolve_user_id(request: Request, user_id: Optional[int]) -> Optional[int]:
    if user_id is not None:
        return user_id
    owner_id = get_request_owner_id(request)
    return get_main_platform_user_id(owner_id)


register_router(
    namespace="project",
    version="v1",
    path="/project",
    router=router,
    tags=["project"],
    description="Project context and data roots management",
    allow_anonymous=True,
)


class DataRoot(BaseModel):
    path: str
    label: Optional[str] = None
    mode: str = "readonly"


class ModelProvider(BaseModel):
    type: Optional[str] = None
    model: Optional[str] = None
    base_url: str
    api_key: str
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
    session_id: Optional[str] = None


class SelectedFilesResponse(BaseModel):
    code: int = 0
    message: str = "success"
    files: list[FileReference]


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: int,
    request: Request,
    user_id: Optional[int] = Query(None, description="Main platform user ID"),
) -> ProjectResponse:
    try:
        resolved_uid = _resolve_user_id(request, user_id)
        project_data = get_project_context(resolved_uid, project_id)
        
        if not project_data:
            return ProjectResponse(
                code=404,
                message="Project not found",
                data=None
            )
        
        data_roots_raw = project_data.get("data_roots", [])
        data_roots = []
        for root in data_roots_raw:
            data_roots.append(DataRoot(
                path=root.get("path", ""),
                label=root.get("label"),
                mode=root.get("mode", "readonly")
            ))
        
        model_provider_raw = project_data.get("model_provider")
        model_provider = None
        if model_provider_raw:
            model_provider = ModelProvider(
                base_url=model_provider_raw.get("base_url", ""),
                api_key=model_provider_raw.get("api_key", "")
            )
        
        project = ProjectData(
            id=project_data.get("id", project_id),
            data_roots=data_roots,
            model_provider=model_provider
        )
        
        return ProjectResponse(
            code=0,
            message="success",
            data=project
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get project context: {e}")
        return ProjectResponse(
            code=500,
            message=f"Failed to get project context: {str(e)}",
            data=None
        )


@router.get("/{project_id}/files", response_model=FileTreeResponse)
async def get_project_files(
    project_id: int,
    request: Request,
    user_id: Optional[int] = Query(None, description="Main platform user ID"),
    path: Optional[str] = Query(None, description="Relative path within data_root"),
    data_root_index: int = Query(0, description="Index of data_root to browse"),
) -> FileTreeResponse:
    try:
        resolved_uid = _resolve_user_id(request, user_id)
        project_data = get_project_context(resolved_uid, project_id)
        if not project_data:
            raise HTTPException(status_code=404, detail="Project not found")
        
        data_roots_raw = project_data.get("data_roots", [])
        if not data_roots_raw:
            return FileTreeResponse(
                code=0,
                message="success",
                data=[]
            )
        
        if data_root_index >= len(data_roots_raw):
            raise HTTPException(status_code=400, detail="Invalid data_root index")
        
        data_root = data_roots_raw[data_root_index]
        root_path = data_root.get("path", "")
        
        if not root_path or not os.path.exists(root_path):
            return FileTreeResponse(
                code=0,
                message="Data root path not accessible",
                data=[]
            )
        
        target_path = Path(root_path)
        if path:
            target_path = target_path / path
            target_path = target_path.resolve()
            root_resolved = Path(root_path).resolve()
            
            try:
                target_path.relative_to(root_resolved)
            except ValueError:
                raise HTTPException(status_code=403, detail="Access denied: path outside data root")
        
        if not target_path.exists():
            return FileTreeResponse(
                code=404,
                message="Path not found",
                data=[]
            )
        
        nodes = _build_file_tree(target_path, root_path)
        
        return FileTreeResponse(
            code=0,
            message="success",
            data=nodes
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get project files: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get project files: {str(e)}")


def _build_file_tree(path: Path, root_path: str, relative_prefix: str = "") -> list[FileTreeNode]:
    nodes = []
    
    try:
        items = sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    except PermissionError:
        logger.warning(f"Permission denied accessing: {path}")
        return nodes
    except Exception as e:
        logger.error(f"Error reading directory {path}: {e}")
        return nodes
    
    for item in items:
        if item.name.startswith("."):
            continue
            
        relative_path = str(item.relative_to(Path(root_path)))
        key = f"{relative_prefix}/{relative_path}" if relative_prefix else relative_path
        
        if item.is_dir():
            children = _build_file_tree(item, root_path, relative_prefix)
            nodes.append(FileTreeNode(
                key=key,
                title=item.name,
                path=str(item),
                is_leaf=False,
                children=children if children else []
            ))
        else:
            nodes.append(FileTreeNode(
                key=key,
                title=item.name,
                path=str(item),
                is_leaf=True
            ))
    
    return nodes


@router.post("/{project_id}/select-files", response_model=SelectedFilesResponse)
async def select_project_files(
    project_id: int,
    request: Request,
    payload: SelectedFilesRequest,
    user_id: Optional[int] = Query(None, description="Main platform user ID"),
) -> SelectedFilesResponse:
    try:
        resolved_uid = _resolve_user_id(request, user_id)
        project_data = get_project_context(resolved_uid, project_id)
        if not project_data:
            raise HTTPException(status_code=404, detail="Project not found")
        
        data_roots_raw = project_data.get("data_roots", [])
        if not data_roots_raw:
            return SelectedFilesResponse(
                code=404,
                message="No data roots configured for this project",
                files=[]
            )
        
        valid_roots = [root.get("path", "") for root in data_roots_raw]
        
        session_upload_dir = None
        if payload.session_id:
            from app.services.session_paths import get_session_upload_dir
            try:
                session_upload_dir = get_session_upload_dir(payload.session_id, create=True)
            except Exception as e:
                logger.warning(f"Failed to create session upload dir: {e}")
        
        files = []
        for selected_path in payload.selected_paths:
            is_valid = False
            matched_root = ""
            source_full_path = None
            
            for root_path in valid_roots:
                if not root_path:
                    continue
                    
                try:
                    full_path = (Path(root_path) / selected_path).resolve()
                    root_resolved = Path(root_path).resolve()
                    full_path.relative_to(root_resolved)
                    
                    if full_path.exists() and full_path.is_file():
                        is_valid = True
                        matched_root = root_path
                        source_full_path = full_path
                        break
                except (ValueError, FileNotFoundError):
                    continue
            
            if is_valid and source_full_path:
                file_ref = FileReference(
                    path=selected_path,
                    name=Path(selected_path).name,
                    data_root_path=matched_root
                )
                
                if session_upload_dir:
                    try:
                        import shutil
                        dest_path = session_upload_dir / file_ref.name
                        
                        original_dest = dest_path
                        counter = 1
                        while dest_path.exists():
                            stem = original_dest.stem
                            suffix = original_dest.suffix
                            dest_path = original_dest.with_name(f"{stem}_{counter}{suffix}")
                            counter += 1
                        
                        shutil.copy2(source_full_path, dest_path)
                        file_ref.path = str(dest_path)
                        logger.info(
                            "Copied project file to session upload: %s -> %s",
                            source_full_path,
                            dest_path
                        )
                    except Exception as e:
                        logger.warning(
                            "Failed to copy file to session upload, using original path: %s",
                            e
                        )
                
                files.append(file_ref)
            else:
                logger.warning(f"Invalid or non-existent file path: {selected_path}")
        
        return SelectedFilesResponse(
            code=0,
            message=f"Selected {len(files)} files",
            files=files
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to select project files: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to select files: {str(e)}")
