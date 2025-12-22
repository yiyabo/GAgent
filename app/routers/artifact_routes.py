"""Runtime artifact listing and preview routes."""

from __future__ import annotations

import mimetypes
from datetime import datetime
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import register_router

RUNTIME_DIR = Path(__file__).parent.parent.parent.resolve() / "runtime"

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


def _resolve_session_dir(session_id: str) -> Path:
    session_id = session_id.strip()
    if not session_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="session_id is required")

    candidates = []
    if session_id.startswith("session_"):
        candidates.append(session_id)
        candidates.append(f"session_{session_id}")
    else:
        candidates.append(f"session_{session_id}")

    for label in candidates:
        session_dir = (RUNTIME_DIR / label).resolve()
        if session_dir.exists() and session_dir.is_dir():
            if str(session_dir).startswith(str(RUNTIME_DIR.resolve())):
                return session_dir

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session artifacts not found")


def _iter_items(
    base_dir: Path,
    *,
    max_depth: int,
    include_dirs: bool,
    limit: int,
    extensions: Optional[List[str]] = None,
) -> List[ArtifactItem]:
    items: List[ArtifactItem] = []
    base_dir = base_dir.resolve()

    for path in base_dir.rglob("*"):
        try:
            rel_path = path.relative_to(base_dir)
        except ValueError:
            continue

        if max_depth > 0 and len(rel_path.parts) > max_depth:
            continue

        if path.is_dir():
            if not include_dirs:
                continue
            items.append(
                ArtifactItem(
                    name=path.name,
                    path=str(rel_path),
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
                path=str(rel_path),
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
    session_dir = _resolve_session_dir(session_id)
    ext_list = None
    if extensions:
        ext_list = [ext.strip().lower().lstrip(".") for ext in extensions.split(",") if ext.strip()]

    items = _iter_items(
        session_dir,
        max_depth=max_depth,
        include_dirs=include_dirs,
        limit=limit,
        extensions=ext_list,
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
    session_dir = _resolve_session_dir(session_id)
    target = (session_dir / path).resolve()

    if not str(target).startswith(str(session_dir)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid artifact path")
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
    session_dir = _resolve_session_dir(session_id)
    target = (session_dir / path).resolve()

    if not str(target).startswith(str(session_dir)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid artifact path")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")

    raw = target.read_bytes()
    truncated = len(raw) > max_bytes
    if truncated:
        raw = raw[:max_bytes]
    content = raw.decode("utf-8", errors="replace")
    return ArtifactTextResponse(path=path, content=content, truncated=truncated)


register_router(
    namespace="artifacts",
    version="v1",
    path="/artifacts",
    router=router,
    tags=["artifacts"],
    description="Runtime artifact listing and preview APIs",
)
