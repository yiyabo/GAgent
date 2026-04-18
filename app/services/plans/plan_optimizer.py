from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.llm import LLMClient
from app.repository.plan_repository import PlanRepository
from app.services.foundation.settings import get_settings
from app.services.llm.llm_service import LLMService

from .plan_models import PlanTree
from .plan_rubric_evaluator import (
    PlanRubricResult,
    evaluate_plan_rubric,
    is_rubric_evaluation_unavailable,
)

logger = logging.getLogger(__name__)

DEFAULT_AUTO_OPTIMIZE_OVERALL_THRESHOLD = 85.0
DEFAULT_AUTO_OPTIMIZE_DIMENSION_THRESHOLD = 70.0
DEFAULT_AUTO_OPTIMIZE_MAX_CHANGES = 8
DEFAULT_OPTIMIZER_PROVIDER = "qwen"
DEFAULT_OPTIMIZER_MODEL = "qwen3.6-plus"

_ALLOWED_AUTO_ACTIONS = frozenset(
    {
        "add_task",
        "update_task",
        "update_description",
        "reorder_task",
    }
)


@dataclass(frozen=True)
class PlanOptimizationProposal:
    summary: str
    rationale: List[str]
    changes: List[Dict[str, Any]]


@dataclass(frozen=True)
class PlanAutoOptimizeConfig:
    overall_threshold: float
    dimension_threshold: float
    max_changes: int


@dataclass(frozen=True)
class PlanAutoOptimizationOutcome:
    plan_tree: PlanTree
    review_before: Optional[PlanRubricResult]
    review_after: Optional[PlanRubricResult]
    generated_changes: List[Dict[str, Any]]
    applied_changes: List[Dict[str, Any]]
    summary: str
    optimization_needed: bool
    auto_generated: bool = False


def _utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _safe_text(value: Any, *, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _root_task_id(tree: PlanTree) -> Optional[int]:
    for node in tree.nodes.values():
        if node.parent_id is None:
            return node.id
    return None


def _as_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return parsed


def _resolve_numeric_threshold(value: Any, fallback: float) -> float:
    parsed = _as_float(value)
    if parsed is None:
        return float(fallback)
    return float(parsed)


def _resolve_max_changes(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(fallback)
    return max(1, parsed)


def get_plan_auto_optimize_config() -> PlanAutoOptimizeConfig:
    settings = get_settings()
    return PlanAutoOptimizeConfig(
        overall_threshold=_resolve_numeric_threshold(
            getattr(settings, "plan_auto_optimize_overall_threshold", None),
            DEFAULT_AUTO_OPTIMIZE_OVERALL_THRESHOLD,
        ),
        dimension_threshold=_resolve_numeric_threshold(
            getattr(settings, "plan_auto_optimize_dimension_threshold", None),
            DEFAULT_AUTO_OPTIMIZE_DIMENSION_THRESHOLD,
        ),
        max_changes=_resolve_max_changes(
            getattr(settings, "plan_auto_optimize_max_changes", None),
            DEFAULT_AUTO_OPTIMIZE_MAX_CHANGES,
        ),
    )


def _coerce_plan_rubric_result(payload: Any) -> Optional[PlanRubricResult]:
    if isinstance(payload, PlanRubricResult):
        return payload
    if not isinstance(payload, dict):
        return None

    # Prefer persisted evaluator payload shape.
    if "overall_score" in payload and "dimension_scores" in payload:
        try:
            return PlanRubricResult(
                plan_id=int(payload.get("plan_id") or 0),
                rubric_version=str(payload.get("rubric_version") or "plan_rubric_v1"),
                evaluator_provider=str(payload.get("evaluator_provider") or "unknown"),
                evaluator_model=str(payload.get("evaluator_model") or "unknown"),
                evaluated_at=str(payload.get("evaluated_at") or _utc_now_iso()),
                overall_score=float(payload.get("overall_score") or 0.0),
                dimension_scores=dict(payload.get("dimension_scores") or {}),
                subcriteria_scores=dict(payload.get("subcriteria_scores") or {}),
                evidence=dict(payload.get("evidence") or {}),
                feedback=dict(payload.get("feedback") or {}),
                rule_evidence=dict(payload.get("rule_evidence") or {}),
            )
        except Exception:
            return None

    # Accept plan_operation review payload shape.
    overall = _as_float(payload.get("rubric_score"))
    dimensions = payload.get("rubric_dimension_scores")
    feedback = payload.get("rubric_feedback")
    evaluator = payload.get("rubric_evaluator")
    if overall is None or not isinstance(dimensions, dict):
        return None
    return PlanRubricResult(
        plan_id=int(payload.get("plan_id") or 0),
        rubric_version=str((evaluator or {}).get("rubric_version") or "plan_rubric_v1"),
        evaluator_provider=str((evaluator or {}).get("provider") or "unknown"),
        evaluator_model=str((evaluator or {}).get("model") or "unknown"),
        evaluated_at=str((evaluator or {}).get("evaluated_at") or _utc_now_iso()),
        overall_score=overall,
        dimension_scores=dict(dimensions),
        subcriteria_scores=dict(payload.get("rubric_subcriteria_scores") or {}),
        evidence={},
        feedback=dict(feedback or {}),
        rule_evidence={},
    )


def plan_review_needs_optimization(
    payload: Any,
    *,
    overall_threshold: Optional[float] = None,
    dimension_threshold: Optional[float] = None,
) -> bool:
    config = get_plan_auto_optimize_config()
    resolved_overall_threshold = (
        config.overall_threshold if overall_threshold is None else float(overall_threshold)
    )
    resolved_dimension_threshold = (
        config.dimension_threshold if dimension_threshold is None else float(dimension_threshold)
    )

    result = _coerce_plan_rubric_result(payload)
    if result is None or is_rubric_evaluation_unavailable(result):
        return False

    if float(result.overall_score) < resolved_overall_threshold:
        return True

    for value in dict(result.dimension_scores or {}).values():
        numeric = _as_float(value)
        if numeric is not None and numeric < resolved_dimension_threshold:
            return True

    feedback = result.feedback if isinstance(result.feedback, dict) else {}
    revisions = feedback.get("actionable_revisions")
    if isinstance(revisions, list) and any(str(item).strip() for item in revisions):
        return True

    return False


def _extract_optimizer_context(result: PlanRubricResult) -> Tuple[List[Tuple[str, float]], List[str], List[str]]:
    dimension_pairs: List[Tuple[str, float]] = []
    for key, value in dict(result.dimension_scores or {}).items():
        numeric = _as_float(value)
        if numeric is None:
            continue
        dimension_pairs.append((str(key), numeric))
    dimension_pairs.sort(key=lambda item: item[1])

    feedback = result.feedback if isinstance(result.feedback, dict) else {}
    weaknesses = [str(item).strip() for item in list(feedback.get("weaknesses") or []) if str(item).strip()]
    revisions = [str(item).strip() for item in list(feedback.get("actionable_revisions") or []) if str(item).strip()]
    return dimension_pairs, weaknesses[:8], revisions[:8]


def _build_plan_optimizer_prompt(
    tree: PlanTree,
    review: PlanRubricResult,
    *,
    max_changes: int,
) -> str:
    outline = tree.to_outline(max_depth=6, max_nodes=140, include_results=False)
    low_dimensions, weaknesses, revisions = _extract_optimizer_context(review)
    dimension_lines = [f"- {name}: {score:.2f}" for name, score in low_dimensions[:6]]
    weak_lines = [f"- {item}" for item in weaknesses]
    revision_lines = [f"- {item}" for item in revisions]

    root_id = _root_task_id(tree)
    schema = {
        "summary": "Short explanation of the optimization strategy.",
        "rationale": ["Why these changes improve plan quality."],
        "changes": [
            {
                "action": "add_task | update_task | update_description | reorder_task",
                "task_id": 0,
                "name": "Optional updated task name",
                "instruction": "Optional updated task instruction",
                "dependencies": [0],
                "parent_id": root_id,
                "description": "Updated plan description when action=update_description",
                "new_position": 0,
            }
        ],
    }

    prompt_lines = [
        "You are a strict plan optimization assistant.",
        "Improve the plan structure using a small set of concrete change objects.",
        "Return only valid JSON. Do not include markdown or commentary outside the JSON.",
        "",
        "=== PLAN OUTLINE ===",
        outline or "(empty plan)",
        "",
        "=== CURRENT RUBRIC SCORE ===",
        f"Overall: {review.overall_score:.2f}",
        *dimension_lines,
    ]
    if weak_lines:
        prompt_lines.extend(["", "=== WEAKNESSES ===", *weak_lines])
    if revision_lines:
        prompt_lines.extend(["", "=== ACTIONABLE REVISIONS ===", *revision_lines])

    prompt_lines.extend(
        [
            "",
            "=== OPTIMIZATION RULES ===",
            f"- Emit at most {max_changes} changes.",
            "- Prefer update_description, add_task, and update_task. Use reorder_task only when ordering is clearly wrong.",
            "- Do NOT delete tasks automatically.",
            "- Preserve the plan goal; focus on missing rationale, missing reproducibility details, quality-control gaps, and overly broad tasks.",
            "- Only reference existing task_id values that appear in the outline.",
            "- For add_task, provide a concrete executable instruction and choose a parent_id.",
            "- For update_task, include only fields that should change.",
            "- If the current plan description is vague, use update_description to make it more explicit and execution-ready.",
            "- If no meaningful optimization is needed, return an empty changes array and explain why in summary.",
            "",
            "=== JSON SCHEMA ===",
            json.dumps(schema, ensure_ascii=False, indent=2),
        ]
    )
    return "\n".join(prompt_lines)


def _normalize_generated_changes(
    tree: PlanTree,
    raw_changes: Any,
) -> List[Dict[str, Any]]:
    if not isinstance(raw_changes, list):
        return []

    root_id = _root_task_id(tree)
    normalized: List[Dict[str, Any]] = []
    existing_ids = set(tree.nodes.keys())

    for raw_change in raw_changes:
        if not isinstance(raw_change, dict):
            continue
        change = dict(raw_change)

        for nested_key in ("updates", "fields", "updated_fields"):
            nested = change.pop(nested_key, None)
            if isinstance(nested, dict):
                for key, value in nested.items():
                    change.setdefault(key, value)

        action = str(change.get("action") or "").strip().lower()
        if action not in _ALLOWED_AUTO_ACTIONS:
            continue

        if action == "add_task":
            name = str(change.get("name") or change.get("task_name") or "").strip()
            instruction = str(change.get("instruction") or change.get("task_instruction") or "").strip()
            if not name or not instruction:
                continue
            parent_id = change.get("parent_id")
            try:
                parent_id_int = int(parent_id) if parent_id is not None else root_id
            except (TypeError, ValueError):
                parent_id_int = root_id
            if parent_id_int is None or parent_id_int not in existing_ids:
                parent_id_int = root_id

            deps = []
            for value in list(change.get("dependencies") or []):
                try:
                    dep_id = int(value)
                except (TypeError, ValueError):
                    continue
                if dep_id in existing_ids and dep_id not in deps:
                    deps.append(dep_id)
            normalized.append(
                {
                    "action": "add_task",
                    "name": name,
                    "instruction": instruction,
                    "parent_id": parent_id_int,
                    **({"dependencies": deps} if deps else {}),
                }
            )
            continue

        if action == "update_description":
            description = str(
                change.get("description")
                or change.get("new_description")
                or change.get("plan_description")
                or ""
            ).strip()
            if description:
                normalized.append({"action": "update_description", "description": description})
            continue

        if action == "reorder_task":
            try:
                task_id = int(change.get("task_id"))
                new_position = int(change.get("new_position"))
            except (TypeError, ValueError):
                continue
            if task_id not in existing_ids:
                continue
            normalized.append(
                {
                    "action": "reorder_task",
                    "task_id": task_id,
                    "new_position": new_position,
                }
            )
            continue

        if action == "update_task":
            try:
                task_id = int(change.get("task_id"))
            except (TypeError, ValueError):
                continue
            if task_id not in existing_ids:
                continue

            payload: Dict[str, Any] = {
                "action": "update_task",
                "task_id": task_id,
            }
            if "name" in change and str(change.get("name") or "").strip():
                payload["name"] = str(change.get("name") or "").strip()
            if "instruction" in change and str(change.get("instruction") or "").strip():
                payload["instruction"] = str(change.get("instruction") or "").strip()
            if "dependencies" in change and isinstance(change.get("dependencies"), list):
                deps = []
                for value in list(change.get("dependencies") or []):
                    try:
                        dep_id = int(value)
                    except (TypeError, ValueError):
                        continue
                    if dep_id in existing_ids and dep_id != task_id and dep_id not in deps:
                        deps.append(dep_id)
                payload["dependencies"] = deps
            if len(payload) > 2:
                normalized.append(payload)

    deduped: List[Dict[str, Any]] = []
    seen = set()
    for change in normalized:
        key = json.dumps(change, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(change)
    return deduped


def _call_optimizer_llm(
    prompt: str,
    *,
    provider: str,
    model: str,
) -> PlanOptimizationProposal:
    try:
        client = LLMClient(provider=provider, model=model, timeout=180)
    except Exception as exc:
        logger.warning("Plan optimizer client initialization failed: %s", exc)
        return PlanOptimizationProposal(
            summary="Optimizer model is unavailable.",
            rationale=[],
            changes=[],
        )
    if not getattr(client, "api_key", None) or not getattr(client, "url", None):
        return PlanOptimizationProposal(
            summary="Optimizer model is unavailable.",
            rationale=[],
            changes=[],
        )

    service = LLMService(client)
    try:
        response = service.chat(prompt, model=model, temperature=0.0)
        payload = service.parse_json_response(response)
    except Exception as exc:
        logger.warning("Plan optimizer LLM call failed: %s", exc)
        return PlanOptimizationProposal(
            summary="Optimizer call failed.",
            rationale=[],
            changes=[],
        )
    if not isinstance(payload, dict):
        logger.warning("Plan optimizer returned invalid JSON")
        return PlanOptimizationProposal(
            summary="Optimizer returned invalid JSON.",
            rationale=[],
            changes=[],
        )

    summary = _safe_text(payload.get("summary"), limit=320)
    rationale = [
        _safe_text(item, limit=220)
        for item in list(payload.get("rationale") or [])
        if str(item).strip()
    ]
    changes = payload.get("changes") if isinstance(payload.get("changes"), list) else []
    return PlanOptimizationProposal(
        summary=summary or "Generated optimization changes from rubric feedback.",
        rationale=rationale[:8],
        changes=changes,
    )


def _build_plan_optimization_metadata(
    *,
    tree: PlanTree,
    review_before: Optional[PlanRubricResult],
    review_after: Optional[PlanRubricResult],
    proposal: PlanOptimizationProposal,
    generated_changes: List[Dict[str, Any]],
    applied_changes: List[Dict[str, Any]],
    auto_generated: bool,
) -> Dict[str, Any]:
    metadata = dict(tree.metadata or {})
    score_before = review_before.overall_score if review_before is not None else None
    score_after = review_after.overall_score if review_after is not None else None
    score_delta = None
    if score_before is not None and score_after is not None:
        score_delta = float(score_after) - float(score_before)

    dimension_score_deltas: Dict[str, float] = {}
    before_dims = dict(review_before.dimension_scores or {}) if review_before is not None else {}
    after_dims = dict(review_after.dimension_scores or {}) if review_after is not None else {}
    for key in sorted(set(before_dims.keys()) | set(after_dims.keys())):
        before_value = _as_float(before_dims.get(key))
        after_value = _as_float(after_dims.get(key))
        if before_value is None or after_value is None:
            continue
        dimension_score_deltas[str(key)] = float(after_value) - float(before_value)

    metadata["plan_optimization"] = {
        "optimized_at": _utc_now_iso(),
        "auto_generated": bool(auto_generated),
        "summary": proposal.summary,
        "rationale": list(proposal.rationale),
        "generated_change_count": len(generated_changes),
        "applied_change_count": len(applied_changes),
        "generated_changes": list(generated_changes),
        "applied_changes": list(applied_changes),
        "overall_score_before": score_before,
        "overall_score_after": score_after,
        "overall_score_delta": score_delta,
        "dimension_score_deltas": dimension_score_deltas,
        "review_before": review_before.to_dict() if review_before is not None else None,
        "review_after": review_after.to_dict() if review_after is not None else None,
    }
    if review_after is not None:
        metadata["plan_evaluation"] = review_after.to_dict()
    elif review_before is not None and "plan_evaluation" not in metadata:
        metadata["plan_evaluation"] = review_before.to_dict()
    return metadata


async def resolve_plan_review_result(
    tree: PlanTree,
    *,
    review_result: Optional[PlanRubricResult] = None,
    evaluator_provider: str = DEFAULT_OPTIMIZER_PROVIDER,
    evaluator_model: str = DEFAULT_OPTIMIZER_MODEL,
) -> Optional[PlanRubricResult]:
    current_review = review_result
    if current_review is None and isinstance(tree.metadata, dict):
        current_review = _coerce_plan_rubric_result(tree.metadata.get("plan_evaluation"))
    if current_review is not None:
        return current_review
    return await asyncio.to_thread(
        evaluate_plan_rubric,
        tree,
        evaluator_provider=evaluator_provider,
        evaluator_model=evaluator_model,
    )


def _preferred_review_provider(
    review: Optional[PlanRubricResult],
    *,
    fallback: str,
) -> str:
    provider = str(getattr(review, "evaluator_provider", "") or "").strip()
    if provider and provider.lower() != "unknown":
        return provider
    return fallback


def _preferred_review_model(
    review: Optional[PlanRubricResult],
    *,
    fallback: str,
) -> str:
    model = str(getattr(review, "evaluator_model", "") or "").strip()
    if model and model.lower() != "unknown":
        return model
    return fallback


async def capture_plan_optimization_outcome(
    *,
    plan_id: int,
    plan_tree_before: PlanTree,
    applied_changes: List[Dict[str, Any]],
    repo: Optional[PlanRepository] = None,
    summary: str,
    generated_changes: Optional[List[Dict[str, Any]]] = None,
    rationale: Optional[List[str]] = None,
    review_before: Optional[PlanRubricResult] = None,
    optimizer_provider: str = DEFAULT_OPTIMIZER_PROVIDER,
    optimizer_model: str = DEFAULT_OPTIMIZER_MODEL,
    auto_generated: bool = False,
    skip_evaluation: bool = False,
) -> PlanAutoOptimizationOutcome:
    repo = repo or PlanRepository()
    updated_tree = repo.get_plan_tree(plan_id)

    # Resolve pre-change review.  When skip_evaluation is True (explicit
    # optimize with no cached review), only use whatever was passed in —
    # do NOT fall through to a live rubric evaluation.
    current_review: Optional[PlanRubricResult] = review_before
    review_after: Optional[PlanRubricResult] = None

    if not skip_evaluation:
        if current_review is None:
            current_review = await resolve_plan_review_result(
                plan_tree_before,
                review_result=review_before,
                evaluator_provider=optimizer_provider,
                evaluator_model=optimizer_model,
            )
        review_after = await asyncio.to_thread(
            evaluate_plan_rubric,
            updated_tree,
            evaluator_provider=_preferred_review_provider(
                current_review,
                fallback=optimizer_provider,
            ),
            evaluator_model=_preferred_review_model(
                current_review,
                fallback=optimizer_model,
            ),
        )

    normalized_generated_changes = list(
        generated_changes if isinstance(generated_changes, list) else applied_changes
    )
    proposal = PlanOptimizationProposal(
        summary=summary,
        rationale=list(rationale or []),
        changes=normalized_generated_changes,
    )
    merged_metadata = _build_plan_optimization_metadata(
        tree=updated_tree,
        review_before=current_review,
        review_after=review_after,
        proposal=proposal,
        generated_changes=normalized_generated_changes,
        applied_changes=applied_changes,
        auto_generated=auto_generated,
    )
    repo.update_plan_metadata(plan_id, merged_metadata)
    updated_tree.metadata = merged_metadata

    return PlanAutoOptimizationOutcome(
        plan_tree=updated_tree,
        review_before=current_review,
        review_after=review_after,
        generated_changes=normalized_generated_changes,
        applied_changes=applied_changes,
        summary=summary,
        optimization_needed=bool(applied_changes),
        auto_generated=auto_generated,
    )


async def auto_optimize_plan(
    *,
    plan_id: int,
    repo: Optional[PlanRepository] = None,
    review_result: Optional[PlanRubricResult] = None,
    max_changes: Optional[int] = None,
    overall_threshold: Optional[float] = None,
    dimension_threshold: Optional[float] = None,
    optimizer_provider: str = DEFAULT_OPTIMIZER_PROVIDER,
    optimizer_model: str = DEFAULT_OPTIMIZER_MODEL,
) -> PlanAutoOptimizationOutcome:
    config = get_plan_auto_optimize_config()
    resolved_max_changes = (
        config.max_changes if max_changes is None else _resolve_max_changes(max_changes, config.max_changes)
    )
    resolved_overall_threshold = (
        config.overall_threshold
        if overall_threshold is None
        else _resolve_numeric_threshold(overall_threshold, config.overall_threshold)
    )
    resolved_dimension_threshold = (
        config.dimension_threshold
        if dimension_threshold is None
        else _resolve_numeric_threshold(dimension_threshold, config.dimension_threshold)
    )

    repo = repo or PlanRepository()
    tree = repo.get_plan_tree(plan_id)

    current_review = await resolve_plan_review_result(
        tree,
        review_result=review_result,
        evaluator_provider=optimizer_provider,
        evaluator_model=optimizer_model,
    )

    if current_review is None or is_rubric_evaluation_unavailable(current_review):
        return PlanAutoOptimizationOutcome(
            plan_tree=tree,
            review_before=current_review,
            review_after=None,
            generated_changes=[],
            applied_changes=[],
            summary="Optimization skipped because rubric evaluation is unavailable.",
            optimization_needed=False,
        )

    if not plan_review_needs_optimization(
        current_review,
        overall_threshold=resolved_overall_threshold,
        dimension_threshold=resolved_dimension_threshold,
    ):
        return PlanAutoOptimizationOutcome(
            plan_tree=tree,
            review_before=current_review,
            review_after=current_review,
            generated_changes=[],
            applied_changes=[],
            summary="Optimization skipped because the plan already meets the score threshold.",
            optimization_needed=False,
        )

    prompt = _build_plan_optimizer_prompt(tree, current_review, max_changes=resolved_max_changes)
    proposal = await asyncio.to_thread(
        _call_optimizer_llm,
        prompt,
        provider=optimizer_provider,
        model=optimizer_model,
    )
    generated_changes = _normalize_generated_changes(tree, proposal.changes)[:resolved_max_changes]
    if not generated_changes:
        return PlanAutoOptimizationOutcome(
            plan_tree=tree,
            review_before=current_review,
            review_after=current_review,
            generated_changes=[],
            applied_changes=[],
            summary=proposal.summary or "No safe optimization changes were generated.",
            optimization_needed=True,
        )

    applied_changes = repo.apply_changes_atomically(plan_id, generated_changes)
    try:
        return await capture_plan_optimization_outcome(
            plan_id=plan_id,
            plan_tree_before=tree,
            applied_changes=applied_changes,
            repo=repo,
            summary=proposal.summary or "Applied rubric-driven optimization changes.",
            generated_changes=generated_changes,
            rationale=proposal.rationale,
            review_before=current_review,
            optimizer_provider=optimizer_provider,
            optimizer_model=optimizer_model,
            auto_generated=True,
        )
    except Exception as exc:
        logger.warning(
            "capture_plan_optimization_outcome failed after auto-optimize "
            "(changes already applied): %s",
            exc,
        )
        return PlanAutoOptimizationOutcome(
            plan_tree=repo.get_plan_tree(plan_id),
            review_before=current_review,
            review_after=None,
            generated_changes=generated_changes,
            applied_changes=applied_changes,
            summary=proposal.summary or "Applied rubric-driven optimization changes (post-commit scoring failed).",
            optimization_needed=True,
        )