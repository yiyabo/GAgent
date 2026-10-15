"""Natural-language turns select actions in the existing model/tool loop."""

import logging
from .request_routing import (
    RequestRoutingDecision,
    manual_deep_think_requested,
    strip_manual_deep_prefix,
    resolve_subject_resolution,
)

MODEL_SELECTION = "model_selects_actions"
logger = logging.getLogger(__name__)
_OLD_EXECUTION_KEYS = (
    "task_id",
    "pending_scope_task_ids",
    "_full_plan_executor_delegate",
    "_current_task_source",
    "explicit_scope_all_blocked",
    "explicit_scope_block_reason",
    "explicit_scope_blocked_task_ids",
    "explicit_task_ids",
    "explicit_task_override",
    "full_plan_execution",
    "plan_create_required",
    "plan_execute_required",
    "plan_execute_after_create_required",
    "plan_review_required",
    "plan_optimize_required",
    "plan_new_requested",
    "plan_conflict_requires_confirmation",
    "task_directory_full",
)


def resolve_model_routing(*, message, history=None, context=None, plan_id=None, current_task_id=None):
    # Explicit UI mode / command syntax is mechanical; no action phrase classifier.
    manual = manual_deep_think_requested(message, context)
    effective = strip_manual_deep_prefix(str(message or ""))
    subject, _ = resolve_subject_resolution(message=effective, history=history, context=context)
    return RequestRoutingDecision(
        request_tier="standard",
        request_route_mode="manual_deepthink" if manual else "auto_deepthink",
        route_reason_codes=[MODEL_SELECTION],
        manual_deep_think=manual,
        thinking_visibility="visible",
        effective_user_message=effective,
        intent_type="chat",
        subject_resolution=subject,
        brevity_hint=False,
        explicit_task_ids=[],
        explicit_task_override=False,
    )


def update_model_context(agent, decision):
    context = agent.extra_context
    selected = context.pop("current_task_id", None)
    if selected is not None:
        context["referenced_task_id"] = selected
    for key in _OLD_EXECUTION_KEYS:
        context.pop(key, None)
    context.update(decision.metadata())
    context["action_selection"] = "model"
    # In chat, the model interprets the current request. Background task tools
    # receive an independent False fence from ToolExecutionContext.
    agent._plan_tree_edit_allowed = None
    plan_id = getattr(agent.plan_session, "plan_id", None)
    index = {"plan_id": plan_id, "referenced_task_id": context.get("referenced_task_id"), "tasks": []}
    if plan_id is not None:
        try:
            from .plan_reference import plan_reference_index
            tree = agent.plan_session.repo.get_plan_tree(plan_id)
            index = plan_reference_index(tree, referenced_task_id=context.get("referenced_task_id"))
        except Exception as exc:
            logger.warning("Plan reference index unavailable: %s", type(exc).__name__)
            index["unavailable"] = True  # Read-tool errors remain visible; never infer a new Plan.
    context["model_plan_index"] = index
