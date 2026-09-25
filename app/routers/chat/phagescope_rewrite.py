"""PhageScope dataset-understanding rewrite cluster of ``agent``.

Moved out of ``agent.py`` per
design/2026-09-24-backend-godfiles-refactor-plan.md §4.8 (module-level cluster
② ``phagescope_rewrite.py``): the marker tables plus the five predicates and the
rewrite that turns a ``create_plan`` response for a local PhageScope dataset
question into a blocking ``phagescope_research`` ``deep_profile`` action.
``agent.py`` re-exports every name, so the facade class call site and
``app/tests/chat/test_request_tier_routing.py``'s direct import are unchanged.

Patch surface: **zero body deviations**.  None of these names is patched
anywhere in ``app/`` or ``app/tests/``, and the only facade binding the cluster
read was ``_extract_declared_absolute_paths_fn`` (the facade's alias of
``guardrails.extract_declared_absolute_paths``), which is imported here directly
from its source module under the same alias name, so every call expression stays
verbatim.  (``_NON_PHAGESCOPE_TABULAR_FILE_EXTS`` /
``_path_is_generic_tabular_file`` / ``_directory_positively_lacks_phagescope_meta_data``
also exist as independent copies in ``request_routing.py`` and
``deep_think/gating_plans.py``; those copies are untouched.)

The module uses its own ``logging.getLogger(__name__)`` (split precedent); the
one log message is byte-identical.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from app.services.deep_think_agent import detect_reasoning_language
from app.services.llm.structured_response import LLMStructuredResponse

from .guardrails import extract_declared_absolute_paths as _extract_declared_absolute_paths_fn

logger = logging.getLogger(__name__)

_PHAGESCOPE_DATASET_UNDERSTANDING_MARKERS = (
    "dataset",
    "data set",
    "数据集",
    "meta_data",
    "metadata",
    "deep profile",
    "understand",
    "understanding",
    "profile",
    "analyze",
    "analyse",
    "analysis",
    "explore",
    "inspect",
    "理解",
    "分析",
    "探索",
    "查看",
)
_PHAGESCOPE_DATASET_STRATEGY_MARKERS = (
    "data splitting",
    "data split",
    "split strategy",
    "model selection",
    "benchmarking",
    "benchmark",
    "biological validation",
    "validation",
    "host prediction",
    "host genus",
    "ml-ready",
    "machine learning",
    "数据划分",
    "划分策略",
    "模型选择",
    "基准",
    "生物学验证",
    "宿主预测",
)
_PLAN_CREATE_NEGATION_MARKERS = (
    "do not create a plan",
    "don't create a plan",
    "dont create a plan",
    "without creating a plan",
    "no plan creation",
    "不要创建 plan",
    "不要创建plan",
    "不要建 plan",
    "不要建plan",
    "不要创建计划",
    "不要生成计划",
    "不要制作计划",
)


def _normalize_phagescope_data_dir(value: Any) -> str:
    text = str(value or "").strip().strip("`'\"")
    return text.rstrip(".,;:)]}>，。；：）】》")


_NON_PHAGESCOPE_TABULAR_FILE_EXTS = (
    ".xlsx",
    ".xls",
    ".xlsm",
    ".csv",
    ".parquet",
    ".feather",
)


def _path_is_generic_tabular_file(value: Any) -> bool:
    return _normalize_phagescope_data_dir(value).lower().endswith(
        _NON_PHAGESCOPE_TABULAR_FILE_EXTS
    )


def _directory_positively_lacks_phagescope_meta_data(value: Any) -> bool:
    text = _normalize_phagescope_data_dir(value)
    if not text:
        return False
    try:
        return os.path.isdir(text) and not os.path.isdir(os.path.join(text, "meta_data"))
    except OSError:
        return False


def _is_phagescope_dataset_understanding_request(
    user_message: str,
    extra_context: Optional[Dict[str, Any]] = None,
) -> bool:
    text = str(user_message or "").strip()
    if not text:
        return False
    lowered = text.lower()
    context = extra_context if isinstance(extra_context, dict) else {}
    subject = context.get("subject_resolution")
    subject_text = ""
    if isinstance(subject, dict):
        subject_text = " ".join(
            str(subject.get(key) or "")
            for key in ("canonical_ref", "display_ref", "raw_ref")
        ).lower()
    if "phagescope" not in lowered and "phagescope" not in subject_text:
        return False
    candidate_path = _extract_phagescope_data_dir_from_context(user_message, extra_context)
    if candidate_path and _path_is_generic_tabular_file(candidate_path):
        return False
    if candidate_path and _directory_positively_lacks_phagescope_meta_data(candidate_path):
        return False
    plan_create_required = bool(context.get("plan_create_required"))
    plan_create_negated = any(marker in lowered for marker in _PLAN_CREATE_NEGATION_MARKERS)
    if plan_create_required and not plan_create_negated:
        return False
    if bool(context.get("plan_review_required")) or bool(context.get("plan_optimize_required")):
        return False
    combined = f"{lowered} {subject_text}"
    return any(marker in combined for marker in _PHAGESCOPE_DATASET_UNDERSTANDING_MARKERS) or any(
        marker in combined for marker in _PHAGESCOPE_DATASET_STRATEGY_MARKERS
    )


def _extract_phagescope_data_dir_from_context(
    user_message: str,
    extra_context: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    context = extra_context if isinstance(extra_context, dict) else {}
    subject = context.get("subject_resolution")
    if isinstance(subject, dict):
        for key in ("display_ref", "canonical_ref", "raw_ref"):
            value = _normalize_phagescope_data_dir(subject.get(key))
            if value and "phagescope" in value.lower():
                return value
    for path in _extract_declared_absolute_paths_fn(user_message):
        value = _normalize_phagescope_data_dir(path)
        if value and "phagescope" in value.lower():
            return value
    return None


def _rewrite_phagescope_dataset_understanding_plan_to_deep_profile(
    structured: LLMStructuredResponse,
    *,
    user_message: str,
    extra_context: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
) -> LLMStructuredResponse:
    if not _is_phagescope_dataset_understanding_request(user_message, extra_context):
        return structured
    actions = list(structured.actions or [])
    data_dir = _extract_phagescope_data_dir_from_context(user_message, extra_context)
    if not data_dir:
        return structured
    has_deep_profile = any(
        action.kind == "tool_operation"
        and str(action.name or "").strip().lower() == "phagescope_research"
        and isinstance(action.parameters, dict)
        and str(action.parameters.get("action") or "").strip().lower() == "deep_profile"
        for action in actions
    )
    if has_deep_profile:
        return structured
    has_create_plan = any(
        action.kind == "plan_operation"
        and str(action.name or "").strip().lower() in {"create_plan", "create"}
        for action in actions
    )
    has_conflicting_tool_action = any(
        action.kind == "tool_operation"
        and str(action.name or "").strip().lower() in {"file_operations", "terminal_session"}
        for action in actions
    )
    if not has_create_plan and not has_conflicting_tool_action:
        return structured
    params: Dict[str, Any] = {
        "action": "deep_profile",
        "data_dir": data_dir,
        "top_n": 30,
    }
    if isinstance(session_id, str) and session_id.strip():
        params["session_id"] = session_id.strip()
    language = detect_reasoning_language(user_message)
    reply = (
        "我会先对本地 PhageScope 数据集执行 deep_profile，基于真实 metadata、大小和划分证据回答；不会创建计划。"
        if language == "zh"
        else "I will deep-profile the local PhageScope dataset first and answer from real metadata, size, and split-readiness evidence; I will not create a plan."
    )
    rewritten = LLMStructuredResponse.model_validate(
        {
            "llm_reply": {"message": reply},
            "actions": [
                {
                    "kind": "tool_operation",
                    "name": "phagescope_research",
                    "parameters": params,
                    "order": 1,
                    "blocking": True,
                    "metadata": {
                        "origin": "phagescope_dataset_understanding_guardrail",
                        "rewrote_from": [
                            action.model_dump() for action in actions if action.kind == "plan_operation"
                        ],
                    },
                }
            ],
        }
    )
    logger.info(
        "[CHAT][PHAGESCOPE_DATASET] Rewrote create_plan to phagescope_research deep_profile for dataset-understanding request"
    )
    return rewritten
