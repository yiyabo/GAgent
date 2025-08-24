from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime

class TaskCreate(BaseModel):
    name: str
    task_type: Optional[str] = "atomic"

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
    relevance: float = 0.0           # How relevant is content to the task
    completeness: float = 0.0        # How complete is the content
    accuracy: float = 0.0            # How accurate is the information
    clarity: float = 0.0             # How clear and understandable
    coherence: float = 0.0           # How logically coherent
    scientific_rigor: float = 0.0    # Scientific accuracy and methodology


class EvaluationResult(BaseModel):
    """Result of content evaluation"""
    overall_score: float
    dimensions: EvaluationDimensions
    suggestions: List[str] = []       # Improvement suggestions
    needs_revision: bool = False      # Whether content needs to be revised
    iteration: int = 0                # Which iteration this evaluation is for
    timestamp: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = None


class EvaluationConfig(BaseModel):
    """Configuration for content evaluation"""
    quality_threshold: float = 0.8    # Minimum acceptable quality score
    max_iterations: int = 3            # Maximum revision iterations
    evaluation_dimensions: List[str] = [
        "relevance", "completeness", "accuracy", "clarity", "coherence"
    ]
    domain_specific: bool = False      # Enable domain-specific evaluation
    strict_mode: bool = False          # Enable strict evaluation mode
    custom_weights: Optional[Dict[str, float]] = None  # Custom dimension weights


class TaskExecutionResult(BaseModel):
    """Extended task execution result with evaluation"""
    task_id: int
    status: str                       # "done", "failed", "needs_review"
    content: Optional[str] = None
    evaluation: Optional[EvaluationResult] = None
    iterations: int = 1               # Number of iterations performed
    execution_time: Optional[float] = None