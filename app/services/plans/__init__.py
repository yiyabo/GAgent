"""Plan management utilities for chat-driven workflows."""

from .artifact_preflight import (
    ArtifactPreflightIssue,
    ArtifactPreflightResult,
    ArtifactPreflightService,
    TaskArtifactContractSnapshot,
)
from .status_resolver import PlanStatusResolver
from .plan_models import PlanNode, PlanTree, PlanSummary
from .plan_executor import (
    ExecutionConfig,
    ExecutionResult,
    ExecutionSummary,
    PlanExecutor,
)
from .dag_models import DAG, DAGNode
from .similarity_matcher import (
    SimilarityMatcher,
    SimpleSimilarityMatcher,
    LLMSimilarityMatcher,
    CachedSimilarityMatcher,
)
from .tree_simplifier import TreeSimplifier, simplify_plan, visualize_plan

__all__ = [
    # Plan models
    "PlanNode",
    "PlanTree",
    "PlanSummary",
    "ArtifactPreflightIssue",
    "ArtifactPreflightResult",
    "ArtifactPreflightService",
    "TaskArtifactContractSnapshot",
    "PlanStatusResolver",
    # Executor
    "PlanExecutor",
    "ExecutionConfig",
    "ExecutionResult",
    "ExecutionSummary",
    # DAG & Simplification
    "DAG",
    "DAGNode",
    "TreeSimplifier",
    "SimilarityMatcher",
    "SimpleSimilarityMatcher",
    "LLMSimilarityMatcher",
    "CachedSimilarityMatcher",
    "simplify_plan",
    "visualize_plan",
]
