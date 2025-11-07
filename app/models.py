from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class TaskCreate(BaseModel):
    name: str
    task_type: Optional[str] = "atomic"
    session_id: Optional[str] = None
    workflow_id: Optional[str] = None
    root_id: Optional[int] = None


class Task(BaseModel):
    """Legacy task model retained for backward compatibility."""

    id: int
    name: str
    metadata: Dict[str, Any] = {}
    parent_id: Optional[int] = None
    path: Optional[str] = None
    depth: Optional[int] = None
    task_type: Optional[str] = None
    session_id: Optional[str] = None
    workflow_id: Optional[str] = None
    root_id: Optional[int] = None
    context_refs: Optional[str] = None
    artifacts: Optional[str] = None


class PlanTaskIn(BaseModel):
    name: str
    instruction: Optional[str] = None
    metadata: Dict[str, Any] = {}


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
    evaluation_dimensions: List[str] = ["relevance", "completeness", "accuracy", "clarity", "coherence"]
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


class TaskExecutionLog(BaseModel):
    id: int
    task_id: int
    workflow_id: Optional[str] = None
    step_type: str
    content: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None


# -----------------------------
# Request models (optional)
# -----------------------------


class ContextOptions(BaseModel):
    include_deps: bool = True
    include_plan: bool = True
    k: int = 5
    manual: Optional[List[int]] = None
    semantic_k: int = 5
    min_similarity: float = 0.1
    include_ancestors: bool = False
    include_siblings: bool = False
    hierarchy_k: int = 3
    max_chars: Optional[int] = None
    per_section_max: Optional[int] = None
    strategy: Optional[str] = None  # "truncate" | "sentence"
    save_snapshot: bool = False
    label: Optional[str] = None
    # executor-specific optional
    generate_embeddings: Optional[bool] = None


class EvaluationOptions(BaseModel):
    max_iterations: int = 3
    quality_threshold: float = 0.8


class RunRequest(BaseModel):
    title: Optional[str] = None
    schedule: Optional[str] = None  # bfs|dag|postorder
    use_context: bool = False
    enable_evaluation: bool = False
    # New flags for orchestration
    use_tools: Optional[bool] = False  # Enable tool-enhanced execution
    auto_decompose: Optional[bool] = False  # Auto run plan decomposition before executing (requires title)
    evaluation_mode: Optional[str] = None  # 'llm' | 'multi_expert' | 'adversarial'
    decompose_max_depth: Optional[int] = None  # Optional depth when auto_decompose
    include_summary: Optional[bool] = False  # Return summary object instead of raw list when true
    auto_assemble: Optional[bool] = False  # If true and title provided, include assembled sections/combined in response
    target_task_id: Optional[int] = None  # New: For single-step execution
    # Output control
    auto_save_output: Optional[bool] = False
    output_filename: Optional[str] = None
    evaluation_options: Optional[EvaluationOptions] = None
    context_options: Optional[ContextOptions] = None


class ContextPreviewRequest(BaseModel):
    include_deps: bool = True
    include_plan: bool = True
    k: int = 5
    max_chars: Optional[int] = None
    per_section_max: Optional[int] = None
    strategy: Optional[str] = None
    semantic_k: int = 5
    min_similarity: float = 0.1
    include_ancestors: bool = False
    include_siblings: bool = False
    hierarchy_k: int = 3
    manual: Optional[List[int]] = None


class ExecuteWithEvaluationRequest(BaseModel):
    max_iterations: int = 3
    quality_threshold: float = 0.8
    use_context: bool = False
    context_options: Optional[ContextOptions] = None


class MoveTaskRequest(BaseModel):
    new_parent_id: Optional[int] = None


class RerunSelectedTasksRequest(BaseModel):
    task_ids: List[int]
    use_context: bool = False
    context_options: Optional[ContextOptions] = None


class RerunTaskSubtreeRequest(BaseModel):
    use_context: bool = False
    context_options: Optional[ContextOptions] = None
    include_parent: bool = True


class TaskUpdate(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[int] = None
