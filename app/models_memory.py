"""
Memory System Models

Integrated memory models that extend the existing system with Memory-MCP capabilities
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MemoryType(str, Enum):
    """Memory type enumeration"""

    CONVERSATION = "conversation"
    EXPERIENCE = "experience"
    KNOWLEDGE = "knowledge"
    CONTEXT = "context"
    TASK_OUTPUT = "task_output"  # Extension: task output memory
    EVALUATION = "evaluation"  # Extension: evaluation memory


class ImportanceLevel(str, Enum):
    """Importance level enumeration"""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    TEMPORARY = "temporary"


class MemoryNote(BaseModel):
    """Memory note model - integrated version"""

    id: str = Field(..., description="Memory unique identifier")
    content: str = Field(..., description="Memory content")
    memory_type: MemoryType = Field(..., description="Memory type")
    importance: ImportanceLevel = Field(default=ImportanceLevel.MEDIUM, description="Importance level")

    # Semantic metadata
    keywords: List[str] = Field(default_factory=list, description="Keyword list")
    context: str = Field(default="General", description="Context description")
    tags: List[str] = Field(default_factory=list, description="Tag list")

    # Related information
    related_task_id: Optional[int] = Field(default=None, description="Related task ID")
    links: List[str] = Field(default_factory=list, description="Related memory ID list")

    # Time information
    created_at: datetime = Field(default_factory=datetime.now, description="Creation time")
    last_accessed: datetime = Field(default_factory=datetime.now, description="Last access time")

    # Usage statistics
    retrieval_count: int = Field(default=0, description="Retrieval count")
    evolution_history: List[Dict[str, Any]] = Field(default_factory=list, description="Evolution history")

    # Vector information
    embedding_generated: bool = Field(default=False, description="Whether embedding vector is generated")
    embedding_model: Optional[str] = Field(default=None, description="Embedding model name")

    @field_validator("content")
    def content_must_not_be_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("Memory content cannot be empty")
        return v.strip()


class SaveMemoryRequest(BaseModel):
    """Save memory request model"""

    content: str = Field(..., description="Memory content")
    memory_type: MemoryType = Field(..., description="Memory type")
    importance: ImportanceLevel = Field(default=ImportanceLevel.MEDIUM, description="Importance level")
    tags: Optional[List[str]] = Field(default=None, description="Tag list")
    related_task_id: Optional[int] = Field(default=None, description="Related task ID")

    # Optional semantic metadata
    keywords: Optional[List[str]] = Field(default=None, description="Keyword list")
    context: Optional[str] = Field(default=None, description="Context description")

    @field_validator("content")
    def content_must_not_be_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("Memory content cannot be empty")
        return v.strip()


class SaveMemoryResponse(BaseModel):
    """Save memory response model"""

    memory_id: str = Field(..., description="Memory ID")
    task_id: Optional[int] = Field(default=None, description="Related task ID")
    memory_type: MemoryType = Field(..., description="Memory type")
    content: str = Field(..., description="Memory content")
    created_at: datetime = Field(..., description="Creation time")
    embedding_generated: bool = Field(..., description="Whether embedding vector is generated")

    # Auto-generated metadata
    keywords: List[str] = Field(default_factory=list, description="Auto-extracted keywords")
    context: str = Field(default="General", description="Auto-generated context")
    tags: List[str] = Field(default_factory=list, description="Auto-generated tags")

    model_config = ConfigDict()


class QueryMemoryRequest(BaseModel):
    """Query memory request model"""

    search_text: str = Field(..., description="Search text")
    memory_types: Optional[List[MemoryType]] = Field(default=None, description="Memory type filter")
    limit: int = Field(default=10, ge=1, le=100, description="Return limit")
    min_similarity: float = Field(default=0.01, ge=0.0, le=1.0, description="Minimum similarity threshold")
    include_task_context: bool = Field(default=False, description="Whether to include task context")


class MemoryItem(BaseModel):
    """Memory item model"""

    memory_id: str = Field(..., description="Memory ID")
    task_id: Optional[int] = Field(default=None, description="Related task ID")
    memory_type: MemoryType = Field(..., description="Memory type")
    content: str = Field(..., description="Memory content")
    similarity: float = Field(..., description="Similarity score")
    created_at: datetime = Field(..., description="Creation time")

    # Metadata
    keywords: List[str] = Field(default_factory=list, description="Keywords")
    context: str = Field(default="General", description="Context")
    tags: List[str] = Field(default_factory=list, description="Tags")
    importance: ImportanceLevel = Field(..., description="Importance level")

    model_config = ConfigDict()


class QueryMemoryResponse(BaseModel):
    """Query memory response model"""

    memories: List[MemoryItem] = Field(..., description="Memory list")
    total: int = Field(..., description="Total count")
    search_time_ms: Optional[float] = Field(default=None, description="Search time (milliseconds)")


class MemoryEvolutionResult(BaseModel):
    """Memory evolution result model"""

    memory_id: str = Field(..., description="Memory ID")
    evolved: bool = Field(..., description="Whether evolution occurred")
    actions_taken: List[str] = Field(default_factory=list, description="Evolution actions taken")
    new_connections: List[str] = Field(default_factory=list, description="Newly established connections")
    updated_tags: List[str] = Field(default_factory=list, description="Updated tags")
    evolution_summary: str = Field(default="", description="Evolution summary")


class MemoryStats(BaseModel):
    """Memory system statistics model"""

    total_memories: int = Field(..., description="Total memory count")
    memory_type_distribution: Dict[str, int] = Field(..., description="Memory type distribution")
    importance_distribution: Dict[str, int] = Field(..., description="Importance distribution")
    average_connections: float = Field(..., description="Average connections")
    embedding_coverage: float = Field(..., description="Embedding vector coverage")
    evolution_count: int = Field(..., description="Evolution count")
