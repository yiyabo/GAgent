"""Pydantic DTOs for the artifact routes (pure data, no behaviour)."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


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


class BatchDownloadFileEntry(BaseModel):
    path: str
    scope: Literal["raw", "deliverables"]
    version: Optional[str] = None


class BatchDownloadRequest(BaseModel):
    files: List[BatchDownloadFileEntry]
