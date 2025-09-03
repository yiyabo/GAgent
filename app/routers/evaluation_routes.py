"""
评估系统相关API端点

包含评估配置、评估历史、评估执行和统计功能。
"""

from datetime import datetime
from fastapi import APIRouter, HTTPException, Body
from typing import Any, Dict, Optional

from ..errors import ValidationError, ErrorCode
from ..execution.executors.enhanced import execute_task_with_evaluation
from ..models import ExecuteWithEvaluationRequest
from ..repository.tasks import default_repo
from ..utils.route_helpers import parse_bool, parse_int, parse_opt_float, sanitize_context_options

router = APIRouter(tags=["evaluation"])


@router.post("/tasks/{task_id}/evaluation/config")
def set_evaluation_config(task_id: int, config: Dict[str, Any] = Body(...)):
    """Set evaluation configuration for a task"""
    try:
        quality_threshold = parse_opt_float(config.get("quality_threshold"), 0.0, 1.0) or 0.8
        max_iterations = parse_int(config.get("max_iterations", 3), default=3, min_value=1, max_value=10)
        evaluation_dimensions = config.get("evaluation_dimensions")
        domain_specific = parse_bool(config.get("domain_specific"), default=False)
        strict_mode = parse_bool(config.get("strict_mode"), default=False)
        custom_weights = config.get("custom_weights")

        # Validate custom weights if provided
        if custom_weights and not isinstance(custom_weights, dict):
            raise HTTPException(status_code=400, detail="custom_weights must be a dictionary")

        # Validate evaluation dimensions if provided
        if evaluation_dimensions and not isinstance(evaluation_dimensions, list):
            raise HTTPException(status_code=400, detail="evaluation_dimensions must be a list")

        default_repo.store_evaluation_config(
            task_id=task_id,
            quality_threshold=quality_threshold,
            max_iterations=max_iterations,
            evaluation_dimensions=evaluation_dimensions,
            domain_specific=domain_specific,
            strict_mode=strict_mode,
            custom_weights=custom_weights,
        )

        return {
            "task_id": task_id,
            "config": {
                "quality_threshold": quality_threshold,
                "max_iterations": max_iterations,
                "evaluation_dimensions": evaluation_dimensions,
                "domain_specific": domain_specific,
                "strict_mode": strict_mode,
                "custom_weights": custom_weights,
            },
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/tasks/{task_id}/evaluation/config")
def get_evaluation_config(task_id: int):
    """Get evaluation configuration for a task"""
    config = default_repo.get_evaluation_config(task_id)
    if not config:
        # Return default configuration
        return {
            "task_id": task_id,
            "config": {
                "quality_threshold": 0.8,
                "max_iterations": 3,
                "evaluation_dimensions": ["relevance", "completeness", "accuracy", "clarity", "coherence"],
                "domain_specific": False,
                "strict_mode": False,
                "custom_weights": None,
            },
            "is_default": True,
        }

    return {"task_id": task_id, "config": config, "is_default": False}


@router.get("/tasks/{task_id}/evaluation/history")
def get_evaluation_history(task_id: int):
    """Get evaluation history for a task"""
    history = default_repo.get_evaluation_history(task_id)

    if not history:
        return {"task_id": task_id, "history": [], "total_iterations": 0}

    return {
        "task_id": task_id,
        "history": history,
        "total_iterations": len(history),
        "latest_score": history[-1]["overall_score"] if history else None,
        "best_score": max(h["overall_score"] for h in history) if history else None,
    }


@router.get("/tasks/{task_id}/evaluation/latest")
def get_latest_evaluation(task_id: int):
    """Get the latest evaluation for a task"""
    evaluation = default_repo.get_latest_evaluation(task_id)
    if not evaluation:
        raise HTTPException(status_code=404, detail="No evaluation found for this task")

    return {"task_id": task_id, "evaluation": evaluation}


@router.post("/tasks/{task_id}/evaluation/override")
def override_evaluation(task_id: int, payload: Dict[str, Any] = Body(...)):
    """Override evaluation result with human feedback"""
    try:
        human_score = parse_opt_float(payload.get("human_score"), 0.0, 1.0)
        human_feedback = payload.get("human_feedback", "")
        override_reason = payload.get("override_reason", "")

        if human_score is None:
            raise HTTPException(status_code=400, detail="human_score is required")

        # Get latest evaluation
        latest_eval = default_repo.get_latest_evaluation(task_id)
        if not latest_eval:
            raise HTTPException(status_code=404, detail="No evaluation found to override")

        # Store override as new evaluation entry
        iteration = latest_eval["iteration"] + 1
        metadata = {
            "override": True,
            "original_score": latest_eval["overall_score"],
            "human_feedback": human_feedback,
            "override_reason": override_reason,
            "override_timestamp": datetime.now().isoformat(),
        }

        default_repo.store_evaluation_history(
            task_id=task_id,
            iteration=iteration,
            content=latest_eval["content"],
            overall_score=human_score,
            dimension_scores=latest_eval["dimension_scores"],
            suggestions=[human_feedback] if human_feedback else [],
            needs_revision=human_score < 0.8,
            metadata=metadata,
        )

        return {
            "task_id": task_id,
            "override_applied": True,
            "new_score": human_score,
            "previous_score": latest_eval["overall_score"],
            "iteration": iteration,
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/tasks/{task_id}/execute/with-evaluation")
def execute_task_with_evaluation_api(task_id: int, payload: Optional[Dict[str, Any]] = Body(None)):
    """Execute task with evaluation-driven iterative improvement"""
    try:
        # Get task info
        task = default_repo.get_task_info(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        # Parse parameters
        try:
            req = ExecuteWithEvaluationRequest.model_validate(payload or {})
        except (ValidationError, ValueError, TypeError):
            req = ExecuteWithEvaluationRequest()
        max_iterations = parse_int(req.max_iterations, default=3, min_value=1, max_value=10)
        quality_threshold = parse_opt_float(req.quality_threshold, 0.0, 1.0) or 0.8
        use_context = bool(req.use_context)
        # Context options
        context_options = None
        if req.context_options is not None:
            context_options = sanitize_context_options(req.context_options.model_dump())

        # Execute with evaluation
        result = execute_task_with_evaluation(
            task=task,
            repo=default_repo,
            max_iterations=max_iterations,
            quality_threshold=quality_threshold,
            use_context=use_context,
            context_options=context_options,
        )

        # Update task status
        default_repo.update_task_status(task_id, result.status)

        # 兼容模拟评估对象（MockEvaluation/MockDimensions）
        eval_payload = None
        if result.evaluation:
            dims = result.evaluation.dimensions
            # 支持 pydantic/dataclass/mock 三种风格
            if hasattr(dims, "model_dump"):
                dim_dict = dims.model_dump()
            elif hasattr(dims, "dict"):
                dim_dict = dims.dict()
            else:
                # 反射读取常见字段
                keys = ["relevance", "completeness", "accuracy", "clarity", "coherence", "scientific_rigor"]
                dim_dict = {k: getattr(dims, k, None) for k in keys if hasattr(dims, k)}

            needs_revision = getattr(result.evaluation, "needs_revision", None)
            if needs_revision is None and hasattr(result, "iterations"):
                # 简单兜底：按分数与阈值推断（若阈值不可得，则按0.8）
                score = getattr(result.evaluation, "overall_score", 0.0)
                needs_revision = bool(score < 0.8)

            eval_payload = {
                "overall_score": result.evaluation.overall_score,
                "dimensions": dim_dict,
                "suggestions": getattr(result.evaluation, "suggestions", []),
                "needs_revision": needs_revision,
            }

        return {
            "task_id": result.task_id,
            "status": result.status,
            "iterations": result.iterations,
            "execution_time": result.execution_time,
            "final_score": result.evaluation.overall_score if result.evaluation else None,
            "evaluation": eval_payload,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Execution failed: {str(e)}") from e


@router.get("/stats")
def get_evaluation_stats():
    """Get overall evaluation system statistics"""
    try:
        stats = default_repo.get_evaluation_stats()
        return {
            "evaluation_stats": stats,
            "system_info": {"evaluation_enabled": True, "default_quality_threshold": 0.8, "default_max_iterations": 3},
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}") from e


@router.delete("/tasks/{task_id}/evaluation/history")
def clear_evaluation_history(task_id: int):
    """Clear all evaluation history for a task"""
    try:
        default_repo.delete_evaluation_history(task_id)
        return {"task_id": task_id, "history_cleared": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear history: {str(e)}") from e
