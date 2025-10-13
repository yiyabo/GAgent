"""
递归分解与评估系统集成模块

将任务分解与质量评估相结合，确保分解结果的质量和合理性。
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
    """评估任务分解的质量

    Args:
        parent_task: 父任务信息
        subtasks: 子任务列表
        repo: 仓储实例

    Returns:
        包含评估结果和建议的字典
    """
    if repo is None:
        repo = default_repo

    # 基础指标
    num_subtasks = len(subtasks)
    parent_name = parent_task.get("name", "")

    issues = []
    suggestions = []
    quality_score = 1.0

    # 1. 检查子任务数量
    if num_subtasks < 2:
        issues.append("子任务数量过少，可能分解不够充分")
        quality_score -= 0.3
        suggestions.append("考虑进一步细化任务分解")
    elif num_subtasks > 8:
        issues.append("子任务数量过多，可能分解过细")
        quality_score -= 0.2
        suggestions.append("考虑合并相关的子任务")

    # 2. 检查子任务名称的覆盖性和重叠度
    subtask_names = [task.get("name", "") for task in subtasks]

    # 简单的重叠检测（基于关键词）
    common_words = set()
    for name in subtask_names:
        words = set(name.lower().split())
        if common_words & words:
            issues.append("子任务间可能存在功能重叠")
            quality_score -= 0.1
            suggestions.append("检查子任务职责边界，避免重叠")
            break
        common_words.update(words)

    # 3. 检查任务类型一致性
    expected_child_type = _get_expected_child_type(parent_task)
    actual_types = [task.get("type", "atomic") for task in subtasks]
    type_consistency = all(t == expected_child_type for t in actual_types)

    if not type_consistency:
        issues.append("子任务类型不一致")
        quality_score -= 0.15
        suggestions.append("统一子任务的类型分类")

    # 4. 检查名称质量
    empty_or_generic = [name for name in subtask_names if not name or "子任务" in name]
    if empty_or_generic:
        issues.append("部分子任务名称不够具体")
        quality_score -= 0.1 * len(empty_or_generic)
        suggestions.append("为所有子任务提供具体、描述性的名称")

    # 确保分数在合理范围内
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
    """根据父任务确定期望的子任务类型"""
    parent_type = parent_task.get("task_type", "atomic")
    depth = parent_task.get("depth", 0)

    if parent_type == "root" or (depth == 0 and parent_type != "atomic"):
        return "composite"
    elif depth >= 2:  # 深层任务的子任务应该是原子的
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
    """带质量评估的任务分解

    Args:
        task_id: 要分解的任务ID
        repo: 仓储实例
        max_subtasks: 最大子任务数
        force: 强制分解
        quality_threshold: 质量阈值
        max_iterations: 最大迭代次数

    Returns:
        分解结果，包含质量评估信息
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

        # 执行基础分解
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

        # 评估分解质量
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

        # 更新最佳结果
        if current_quality > best_quality:
            best_quality = current_quality
            best_result = iteration_result

        # 如果质量满足阈值，提前结束
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

        # 如果不是最后一次迭代，清理当前结果准备重试
        if iteration < max_iterations - 1:
            # 删除刚创建的子任务以便重试
            for subtask in subtasks:
                try:
                    # 这里应该删除创建的子任务，但为了安全起见，我们暂时跳过
                    # 实际实现中可能需要更复杂的回滚机制
                    pass
                except Exception as e:
                    _EVAL_DECOMP_LOGGER.warning(
                        {
                            "event": "decompose_with_evaluation.cleanup_failed",
                            "subtask_id": subtask.get("id"),
                            "error": str(e),
                        }
                    )

    # 返回最佳结果或最后一次结果
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
    """带质量检查的分解建议

    Args:
        task: 任务信息
        repo: 仓储实例
        min_complexity_score: 最小复杂度分数

    Returns:
        分解建议和详细分析
    """
    if repo is None:
        repo = default_repo

    # 基础分解判断
    basic_should_decompose = should_decompose_task(task, repo)
    task_type = determine_task_type(task)

    # 获取任务详情进行更深入的分析
    task_id = task.get("id")
    task_name = task.get("name", "")
    task_prompt = repo.get_task_input_prompt(task_id) if task_id else ""

    # 复杂度评估
    from .recursive_decomposition import evaluate_task_complexity

    complexity = evaluate_task_complexity(task_name, task_prompt)

    # 转换为数值分数便于比较
    complexity_scores = {"low": 0.3, "medium": 0.6, "high": 0.9}
    complexity_score = complexity_scores.get(complexity, 0.5)

    # 综合判断
    recommendations = []
    should_decompose = basic_should_decompose

    if not basic_should_decompose:
        if task_type == TaskType.ATOMIC:
            recommendations.append("任务已是原子级别，不建议进一步分解")
        elif task.get("depth", 0) >= 2:
            recommendations.append("任务已达到最大分解深度")
        else:
            recommendations.append("任务当前不需要分解")
    else:
        if complexity_score >= min_complexity_score:
            recommendations.append("任务复杂度较高，建议进行分解")
            if complexity_score >= 0.8:
                recommendations.append("建议分解为4-6个子任务")
            else:
                recommendations.append("建议分解为2-4个子任务")
        else:
            should_decompose = False
            recommendations.append("任务复杂度不足，分解可能得不偿失")

    # 现有子任务检查
    children_count = 0
    if task_id:
        try:
            children = repo.get_children(task_id)
            children_count = len(children)
            if children_count > 0:
                recommendations.append(f"任务已有{children_count}个子任务")
                if not should_decompose:
                    recommendations.append("建议查看现有子任务是否需要进一步优化")
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
