"""Execution command routes."""

from __future__ import annotations

import logging
from typing import Dict, List, Literal, Optional, Sequence, Union

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, validator

from ..services.execution import command_runner, workspace_manager
from . import register_router

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/execution", tags=["execution"])


class ShellCommandRequest(BaseModel):
    owner: str = Field(..., min_length=1, max_length=128)
    command: Union[str, Sequence[str]]
    timeout: Optional[int] = Field(default=None, gt=0, le=600)
    reset_workspace: bool = False
    env: Optional[Dict[str, str]] = Field(default=None, description="Extra environment variables")

    @validator("command")
    def _validate_command(cls, value: Union[str, Sequence[str]]) -> Union[str, Sequence[str]]:
        if isinstance(value, (list, tuple)):
            if not value:
                raise ValueError("command list cannot be empty")
            if not all(isinstance(item, str) and item.strip() for item in value):
                raise ValueError("command list must contain non-empty strings")
        else:
            if not value or not value.strip():
                raise ValueError("command string cannot be empty")
        return value


class ShellCommandResponse(BaseModel):
    command: List[str]
    stdout: str
    stderr: str
    exit_code: Optional[int]
    duration: float
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool


class WorkspaceItem(BaseModel):
    name: str
    path: str
    type: Literal["file", "directory"]
    size: int = 0


class WorkspaceContentsResponse(BaseModel):
    owner: str
    path: str
    items: List[WorkspaceItem]
    count: int


class OperationStatusResponse(BaseModel):
    success: bool
    message: Optional[str] = None


class WriteFileRequest(BaseModel):
    owner: str = Field(..., min_length=1, max_length=128)
    relative_path: str = Field(..., min_length=1)
    content: str = Field(default="")
    reset_workspace: bool = False


@router.post("/shell", response_model=ShellCommandResponse)
async def execute_shell_command(payload: ShellCommandRequest) -> ShellCommandResponse:
    workspace = await workspace_manager.prepare_workspace(payload.owner, reset=payload.reset_workspace)
    try:
        argv = command_runner.parse_command(payload.command)
        result = await command_runner.run_shell_command(
            argv,
            cwd=workspace,
            timeout=payload.timeout,
            env=payload.env,
        )
    except ValueError as exc:
        logger.warning("Shell execution rejected: %s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Shell execution failed for owner=%s", payload.owner)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Command execution failed") from exc

    return ShellCommandResponse(**result.to_dict())


@router.get("/workspaces/{owner}", response_model=WorkspaceContentsResponse)
async def list_workspace(owner: str) -> WorkspaceContentsResponse:
    workspace = await workspace_manager.prepare_workspace(owner, reset=False)
    listing = await workspace_manager.list_workspace(owner)
    if not listing.get("success"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=listing.get("error", "Workspace not found"))

    items = [
        WorkspaceItem(
            name=item.get("name", ""),
            path=item.get("path", ""),
            type=item.get("type", "file"),
            size=int(item.get("size", 0) or 0),
        )
        for item in listing.get("items", [])
    ]
    return WorkspaceContentsResponse(
        owner=owner,
        path=str(workspace),
        items=items,
        count=len(items),
    )


@router.delete("/workspaces/{owner}", response_model=OperationStatusResponse)
async def delete_workspace(owner: str) -> OperationStatusResponse:
    response = await workspace_manager.cleanup_workspace(owner)
    success = response.get("success", False)
    message = response.get("error") or response.get("message")
    if not success and response.get("error"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=response["error"])
    return OperationStatusResponse(success=True, message=message)


@router.post("/workspaces/{owner}/files", response_model=OperationStatusResponse)
async def write_workspace_file(owner: str, payload: WriteFileRequest) -> OperationStatusResponse:
    if owner != payload.owner:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Owner mismatch")

    workspace = await workspace_manager.prepare_workspace(owner, reset=payload.reset_workspace)
    relative_path = payload.relative_path.lstrip("/\\")
    target = workspace / relative_path
    response = await workspace_manager.write_file(owner, relative_path, payload.content)
    if not response.get("success"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=response.get("error", "Write failed"))
    return OperationStatusResponse(success=True, message=f"Wrote {target}")


register_router(
    namespace="execution",
    version="v1",
    path="/execution",
    router=router,
    tags=["execution"],
    description="Local shell execution and workspace management APIs",
)
