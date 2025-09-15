from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ContextCreate(BaseModel):
    label: str
    content: str


class TaskCreate(BaseModel):
    name: str
    task_type: Optional[str] = "atomic"
    parent_id: Optional[int] = None
    plan_id: Optional[int] = None
    prompt: Optional[str] = None
    contexts: Optional[List[ContextCreate]] = None


class TaskUpdate(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[int] = None
    task_type: Optional[str] = None


class TaskSummary(BaseModel):
    id: int
    name: str


class Task(BaseModel):
    id: int
    name: str
    status: str


class PlanTaskIn(BaseModel):
    name: str
    prompt: Optional[str] = None
    priority: Optional[int] = None


class PlanIn(BaseModel):
    title: str
    tasks: List[PlanTaskIn]


class PlanProposal(BaseModel):
    goal: str
    title: Optional[str] = None
    sections: Optional[int] = None
    style: Optional[str] = None
    notes: Optional[str] = None


class PlanApproval(BaseModel):
    title: str
    tasks: List[PlanTaskIn]


# Evaluation System Models
class EvaluationDimensions(BaseModel):
    """Individual evaluation dimension scores"""

    relevance: float = 0.0  # How relevant is content to the task
    completeness: float = 0.0  # How complete is the content
    accuracy: float = 0.0  # How accurate is the information
    clarity: float = 0.0  # How clear and understandable
    coherence: float = 0.0  # How logically coherent
    scientific_rigor: float = 0.0  # Scientific accuracy and methodology


class EvaluationResult(BaseModel):
    """Result of content evaluation"""

    overall_score: float
    dimensions: EvaluationDimensions
    suggestions: List[str] = []  # Improvement suggestions
    needs_revision: bool = False  # Whether content needs to be revised
    iteration: int = 0  # Which iteration this evaluation is for
    timestamp: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = None


class EvaluationConfig(BaseModel):
    """Configuration for content evaluation"""

    quality_threshold: float = 0.8  # Minimum acceptable quality score
    max_iterations: int = 3  # Maximum revision iterations
    evaluation_dimensions: List[str] = [
        "relevance",
        "completeness",
        "accuracy",
        "clarity",
        "coherence",
    ]
    domain_specific: bool = False  # Enable domain-specific evaluation
    strict_mode: bool = False  # Enable strict evaluation mode
    custom_weights: Optional[Dict[str, float]] = None  # Custom dimension weights


class TaskExecutionResult(BaseModel):
    """Extended task execution result with evaluation"""

    task_id: int
    status: str  # "done", "failed", "needs_review"
    content: Optional[str] = None
    evaluation: Optional[EvaluationResult] = None
    iterations: int = 1  # Number of iterations performed
    execution_time: Optional[float] = None


# Plan Management System Models
class Plan(BaseModel):
    """研究计划/项目的完整定义"""

    id: int
    title: str
    description: Optional[str] = None
    status: str = "active"  # active/completed/archived
    created_at: datetime
    updated_at: datetime
    config_json: Optional[Dict[str, Any]] = None
    task_count: int = 0  # 关联的任务数量
    progress: float = 0.0  # 完成进度 0-1


class PlanTask(BaseModel):
    """任务与计划的精确关联"""

    plan_id: int
    task_id: int
    task_category: str = "general"  # root/chapter/section/atomic
    created_at: datetime


class PlanCreate(BaseModel):
    """创建新计划的请求模型"""

    title: str
    description: Optional[str] = None
    config_json: Optional[Dict[str, Any]] = None


class PlanUpdate(BaseModel):
    """更新计划的请求模型"""

    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    config_json: Optional[Dict[str, Any]] = None


class PlanSummary(BaseModel):
    """计划汇总信息"""

    id: int
    title: str
    description: Optional[str] = None
    task_count: int
    completed_count: int
    status: str
    progress: float
    created_at: datetime


class PlanWithTasks(BaseModel):
    """完整计划包含所有关联任务"""

    plan: Plan
    tasks: List[PlanTask]
    task_details: Optional[List[Dict[str, Any]]] = None


# Chat System Models
class Message(BaseModel):
    id: int
    conversation_id: int
    sender: str
    text: str
    created_at: datetime

    class Config:
        from_attributes = True


class Conversation(BaseModel):
    id: int
    title: str
    created_at: datetime

    class Config:
        from_attributes = True


class ConversationWithMessages(Conversation):
    messages: List[Message] = []


class ConversationCreate(BaseModel):
    title: str


class MessageCreate(BaseModel):
    text: str
    sender: str = "user"
    plan_id: Optional[int] = None
    confirmed: Optional[bool] = False
