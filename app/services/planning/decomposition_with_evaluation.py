"""Recursive decomposition and evaluation integration module.

Combines task decomposition with quality evaluation to ensure decomposition
quality and structural reasonableness.
"""

import logging
from typing import Any, Dict, List, Optional

from ...interfaces import TaskRepository
from ...repository.tasks import default_repo
from .recursive_decomposition import TaskType
from .recursive_decomposition import decompose_task as base_decompose_task
from .recursive_decomposition import determine_task_type, should_decompose_task

_EVAL_DECOMP_LOGGER = logging.getLogger("app.decomposition.evaluation")


def evaluate_decomposition_quality(
    parent_task: Dict[str, Any], subtasks: List[Dict[str, Any]], repo: TaskRepository = None
) -> Dict[str, Any]:
    """Evaluate quality of task decomposition.

    Args:
        parent_task: Parent task info
        subtasks: Subtask list
        repo: Repository instance

    Returns:
        Dict containing evaluation outcomes and suggestions
    """
    if repo is None:
        repo = default_repo

    # Basic metrics
    num_subtasks = len(subtasks)
    parent_name = parent_task.get("name", "")

    issues = []
    suggestions = []
    quality_score = 1.0

    # 1. Check number of subtasks
    if num_subtasks < 2:
        issues.append("Too few subtasks; decomposition may be insufficient.")
        quality_score -= 0.3
        suggestions.append("Consider refining the decomposition further.")
    elif num_subtasks > 8:
        issues.append("Too many subtasks; decomposition may be overly fine-grained.")
        quality_score -= 0.2
        suggestions.append("Consider merging related subtasks.")

    # 2. Check coverage and overlap in subtask names
    subtask_names = [task.get("name", "") for task in subtasks]

    # Simple overlap detection (keyword-based)
    common_words = set()
    for name in subtask_names:
        words = set(name.lower().split())
        if common_words & words:
            issues.append("Potential functional overlap detected among subtasks.")
            quality_score -= 0.1
            suggestions.append("Review subtask responsibility boundaries to avoid overlap.")
            break
        common_words.update(words)

    # 3. Check consistency of task types
    expected_child_type = _get_expected_child_type(parent_task)
    actual_types = [task.get("type", "atomic") for task in subtasks]
    type_consistency = all(t == expected_child_type for t in actual_types)

    if not type_consistency:
        issues.append("Subtask types are inconsistent.")
        quality_score -= 0.15
        suggestions.append("Unify type classification across subtasks.")

    # 4. Check naming quality
    empty_or_generic = [name for name in subtask_names if not name or "subtask" in name.lower()]
    if empty_or_generic:
        issues.append("Some subtask names are not specific enough.")
        quality_score -= 0.1 * len(empty_or_generic)
        suggestions.append("Provide concrete, descriptive names for all subtasks.")

    # Clamp score into valid range
    quality_score = max(0.0, min(1.0, quality_score))

    return {
        "quality_score": round(quality_score, 3),
        "num_subtasks": num_subtasks,
        "issues": issues,
        "suggestions": suggestions,
        "type_consistency": type_consistency,
        "expected_child_type": expected_child_type,
        "needs_refinement": quality_score < 0.7,
    }


def _get_expected_child_type(parent_task: Dict[str, Any]) -> str:
    """Determine expected child-task type based on parent task."""
    parent_type = parent_task.get("task_type", "atomic")
    depth = parent_task.get("depth", 0)

    if parent_type == "root" or (depth == 0 and parent_type != "atomic"):
        return "composite"
    elif depth >= 2:  # Children of deep tasks should be atomic.
        return "atomic"
    else:
        return "composite"


def decompose_task_with_evaluation(
    task_id: int,
    repo: TaskRepository = None,
    max_subtasks: int = 8,
    force: bool = False,
    quality_threshold: float = 0.7,
    max_iterations: int = 2,
) -> Dict[str, Any]:
    """Task decomposition with quality evaluation.

    Args:
        task_id: Target task ID to decompose
        repo: Repository instance
        max_subtasks: Maximum number of subtasks
        force: Force decomposition
        quality_threshold: Quality threshold
        max_iterations: Maximum iteration count

    Returns:
        Decomposition result including quality evaluation
    """
    if repo is None:
        repo = default_repo

    parent_task = repo.get_task_info(task_id)
    if not parent_task:
        return {"success": False, "error": "Task not found"}

    best_result = None
    best_quality = 0.0
    iteration_results = []

    for iteration in range(max_iterations):
        _EVAL_DECOMP_LOGGER.debug(
            {
                "event": "decompose_with_evaluation.iteration",
                "task_id": task_id,
                "iteration": iteration + 1,
                "max_iterations": max_iterations,
            }
        )

        # Run base decomposition
        decomp_result = base_decompose_task(task_id=task_id, repo=repo, max_subtasks=max_subtasks, force=force)

        if not decomp_result.get("success"):
            iteration_results.append(
                {
                    "iteration": iteration + 1,
                    "success": False,
                    "error": decomp_result.get("error"),
                    "quality_evaluation": None,
                }
            )
            continue

        # Evaluate decomposition quality
        subtasks = decomp_result.get("subtasks", [])
        quality_eval = evaluate_decomposition_quality(parent_task, subtasks, repo)

        iteration_result = {
            "iteration": iteration + 1,
            "success": True,
            "decomposition": decomp_result,
            "quality_evaluation": quality_eval,
        }
        iteration_results.append(iteration_result)

        current_quality = quality_eval["quality_score"]

        # Update best result
        if current_quality > best_quality:
            best_quality = current_quality
            best_result = iteration_result

        # Stop early when quality threshold is reached
        if current_quality >= quality_threshold:
            _EVAL_DECOMP_LOGGER.debug(
                {
                    "event": "decompose_with_evaluation.threshold_met",
                    "task_id": task_id,
                    "quality_score": current_quality,
                    "threshold": quality_threshold,
                    "iteration": iteration + 1,
                }
            )
            break

        # If not final iteration, clean current result before retry
        if iteration < max_iterations - 1:
            # Delete newly created subtasks before retry
            for subtask in subtasks:
                try:
                    # We should delete created subtasks here, but skip for safety.
                    # Real implementations may require a more robust rollback.
                    pass
                except Exception as e:
                    _EVAL_DECOMP_LOGGER.warning(
                        {
                            "event": "decompose_with_evaluation.cleanup_failed",
                            "subtask_id": subtask.get("id"),
                            "error": str(e),
                        }
                    )

    # Return best result or final attempted result
    final_result = best_result or iteration_results[-1] if iteration_results else None

    if not final_result:
        return {
            "success": False,
            "error": "Failed to decompose task after all iterations",
            "iterations_attempted": max_iterations,
        }

    return {
        "success": final_result["success"],
        "task_id": task_id,
        "subtasks": final_result["decomposition"]["subtasks"] if final_result.get("decomposition") else [],
        "quality_evaluation": final_result["quality_evaluation"],
        "iterations_performed": len(iteration_results),
        "best_quality_score": best_quality,
        "quality_threshold": quality_threshold,
        "meets_threshold": best_quality >= quality_threshold,
        "iteration_history": iteration_results,
    }


def should_decompose_with_quality_check(
    task: Dict[str, Any], repo: TaskRepository = None, min_complexity_score: float = 0.6
) -> Dict[str, Any]:
    """Decomposition recommendation with quality checks.

    Args:
        task: Task info
        repo: Repository instance
        min_complexity_score: Minimum complexity score

    Returns:
        Decomposition recommendation and detailed analysis
    """
    if repo is None:
        repo = default_repo

    # Basic decomposition judgment
    basic_should_decompose = should_decompose_task(task, repo)
    task_type = determine_task_type(task)

    # Load task details for deeper analysis
    task_id = task.get("id")
    task_name = task.get("name", "")
    task_prompt = repo.get_task_input_prompt(task_id) if task_id else ""

    # Complexity assessment
    from .recursive_decomposition import evaluate_task_complexity

    complexity = evaluate_task_complexity(task_name, task_prompt)

    # Convert to numeric score for comparison
    complexity_scores = {"low": 0.3, "medium": 0.6, "high": 0.9}
    complexity_score = complexity_scores.get(complexity, 0.5)

    # Combined judgment
    recommendations = []
    should_decompose = basic_should_decompose

    if not basic_should_decompose:
        if task_type == TaskType.ATOMIC:
            recommendations.append("Task is already atomic; further decomposition is not recommended.")
        elif task.get("depth", 0) >= 2:
            recommendations.append("Task has reached maximum decomposition depth.")
        else:
            recommendations.append("Task currently does not need decomposition.")
    else:
        if complexity_score >= min_complexity_score:
            recommendations.append("Task complexity is high; decomposition is recommended.")
            if complexity_score >= 0.8:
                recommendations.append("Recommended split: 4-6 subtasks.")
            else:
                recommendations.append("Recommended split: 2-4 subtasks.")
        else:
            should_decompose = False
            recommendations.append("Task complexity is low; decomposition may not be worth the cost.")

    # Existing-subtask check
    children_count = 0
    if task_id:
        try:
            children = repo.get_children(task_id)
            children_count = len(children)
            if children_count > 0:
                recommendations.append(f"Task already has {children_count} subtasks.")
                if not should_decompose:
                    recommendations.append("Consider optimizing existing subtasks instead.")
        except Exception:
            pass

    return {
        "should_decompose": should_decompose,
        "task_type": task_type.value,
        "complexity": complexity,
        "complexity_score": complexity_score,
        "depth": task.get("depth", 0),
        "existing_children": children_count,
        "recommendations": recommendations,
        "analysis": {
            "basic_decomposition_eligible": basic_should_decompose,
            "complexity_sufficient": complexity_score >= min_complexity_score,
            "within_depth_limit": task.get("depth", 0) < 2,
            "not_atomic": task_type != TaskType.ATOMIC,
        },
    }
