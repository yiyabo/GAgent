"""
Memory System Models

Integrated memory models that extend the existing system with Memory-MCP capabilities
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MemoryType(str, Enum):
    """记忆类型枚举"""

    CONVERSATION = "conversation"
    EXPERIENCE = "experience"
    KNOWLEDGE = "knowledge"
    CONTEXT = "context"
    TASK_OUTPUT = "task_output"  # 扩展：任务输出记忆
    EVALUATION = "evaluation"  # 扩展：评估记忆


class ImportanceLevel(str, Enum):
    """重要性级别枚举"""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    TEMPORARY = "temporary"


class MemoryNote(BaseModel):
    """记忆笔记模型 - 集成版本"""

    id: str = Field(..., description="记忆唯一标识")
    content: str = Field(..., description="记忆内容")
    memory_type: MemoryType = Field(..., description="记忆类型")
    importance: ImportanceLevel = Field(default=ImportanceLevel.MEDIUM, description="重要性级别")

    # 语义元数据
    keywords: List[str] = Field(default_factory=list, description="关键词列表")
    context: str = Field(default="General", description="上下文描述")
    tags: List[str] = Field(default_factory=list, description="标签列表")

    # 关联信息
    related_task_id: Optional[int] = Field(default=None, description="关联任务ID")
    links: List[str] = Field(default_factory=list, description="关联记忆ID列表")

    # 时间信息
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    last_accessed: datetime = Field(default_factory=datetime.now, description="最后访问时间")

    # 使用统计
    retrieval_count: int = Field(default=0, description="检索次数")
    evolution_history: List[Dict[str, Any]] = Field(default_factory=list, description="进化历史")

    # 向量信息
    embedding_generated: bool = Field(default=False, description="是否已生成嵌入向量")
    embedding_model: Optional[str] = Field(default=None, description="嵌入模型名称")

    @field_validator("content")
    def content_must_not_be_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("Memory content cannot be empty")
        return v.strip()


class SaveMemoryRequest(BaseModel):
    """保存记忆请求模型"""

    content: str = Field(..., description="记忆内容")
    memory_type: MemoryType = Field(..., description="记忆类型")
    importance: ImportanceLevel = Field(default=ImportanceLevel.MEDIUM, description="重要性级别")
    tags: Optional[List[str]] = Field(default=None, description="标签列表")
    related_task_id: Optional[int] = Field(default=None, description="关联任务ID")

    # 可选的语义元数据
    keywords: Optional[List[str]] = Field(default=None, description="关键词列表")
    context: Optional[str] = Field(default=None, description="上下文描述")

    @field_validator("content")
    def content_must_not_be_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("Memory content cannot be empty")
        return v.strip()


class SaveMemoryResponse(BaseModel):
    """保存记忆响应模型"""

    memory_id: str = Field(..., description="记忆ID")
    task_id: Optional[int] = Field(default=None, description="关联任务ID")
    memory_type: MemoryType = Field(..., description="记忆类型")
    content: str = Field(..., description="记忆内容")
    created_at: datetime = Field(..., description="创建时间")
    embedding_generated: bool = Field(..., description="是否已生成嵌入向量")

    # 自动生成的元数据
    keywords: List[str] = Field(default_factory=list, description="自动提取的关键词")
    context: str = Field(default="General", description="自动生成的上下文")
    tags: List[str] = Field(default_factory=list, description="自动生成的标签")

    model_config = ConfigDict()


class QueryMemoryRequest(BaseModel):
    """查询记忆请求模型"""

    search_text: str = Field(..., description="搜索文本")
    memory_types: Optional[List[MemoryType]] = Field(default=None, description="记忆类型过滤")
    limit: int = Field(default=10, ge=1, le=100, description="返回数量限制")
    min_similarity: float = Field(default=0.01, ge=0.0, le=1.0, description="最小相似度阈值")
    include_task_context: bool = Field(default=False, description="是否包含任务上下文")


class MemoryItem(BaseModel):
    """记忆项模型"""

    memory_id: str = Field(..., description="记忆ID")
    task_id: Optional[int] = Field(default=None, description="关联任务ID")
    memory_type: MemoryType = Field(..., description="记忆类型")
    content: str = Field(..., description="记忆内容")
    similarity: float = Field(..., description="相似度分数")
    created_at: datetime = Field(..., description="创建时间")

    # 元数据
    keywords: List[str] = Field(default_factory=list, description="关键词")
    context: str = Field(default="General", description="上下文")
    tags: List[str] = Field(default_factory=list, description="标签")
    importance: ImportanceLevel = Field(..., description="重要性级别")

    model_config = ConfigDict()


class QueryMemoryResponse(BaseModel):
    """查询记忆响应模型"""

    memories: List[MemoryItem] = Field(..., description="记忆列表")
    total: int = Field(..., description="总数量")
    search_time_ms: Optional[float] = Field(default=None, description="搜索耗时(毫秒)")


class MemoryEvolutionResult(BaseModel):
    """记忆进化结果模型"""

    memory_id: str = Field(..., description="记忆ID")
    evolved: bool = Field(..., description="是否发生进化")
    actions_taken: List[str] = Field(default_factory=list, description="执行的进化动作")
    new_connections: List[str] = Field(default_factory=list, description="新建立的连接")
    updated_tags: List[str] = Field(default_factory=list, description="更新的标签")
    evolution_summary: str = Field(default="", description="进化摘要")


class MemoryStats(BaseModel):
    """记忆系统统计模型"""

    total_memories: int = Field(..., description="总记忆数量")
    memory_type_distribution: Dict[str, int] = Field(..., description="记忆类型分布")
    importance_distribution: Dict[str, int] = Field(..., description="重要性分布")
    average_connections: float = Field(..., description="平均连接数")
    embedding_coverage: float = Field(..., description="嵌入向量覆盖率")
    evolution_count: int = Field(..., description="进化次数")
