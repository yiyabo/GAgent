"""Plan-related helper functions extracted from the chat router.

These helpers operate on an agent instance (or similar context) and handle
plan binding, tree refresh, decomposition, and persistence.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from app.services.llm.structured_response import LLMStructuredResponse
from app.services.plans.decomposition_jobs import start_decomposition_job_thread

from .models import AgentStep

if TYPE_CHECKING:
    from app.services.plans.plan_models import PlanTree

logger = logging.getLogger(__name__)


def build_suggestions(
    agent: Any, structured: LLMStructuredResponse, steps: List[AgentStep]
) -> List[str]:
    base_suggestions: List[str] = []
    failures = [step for step in steps if not step.success]
    if failures:
        base_suggestions.append(
            "Some actions failed; provide more specific parameters or try again later."
        )
    if not structured.actions:
        base_suggestions.append("Continue describing the tasks or plans you want to handle.")
        if agent.plan_session.plan_id is None:
            base_suggestions.append("I can create new plans or list existing ones.")
    else:
        base_suggestions.append("If you need to execute those actions, supply the required details and confirm.")
    if structured.actions and structured.actions[0].kind == "context_request":
        base_suggestions.append("After reviewing the returned subgraph, you may provide the next instruction.")
    return base_suggestions


def require_plan_bound(agent: Any) -> "PlanTree":
    if agent.plan_session.plan_id is None:
        raise ValueError("The session is not bound to any plan, so tasks or context actions cannot be executed.")
    try:
        return agent.plan_session.ensure()
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc


def refresh_plan_tree(agent: Any, force_reload: bool = True) -> None:
    if agent.plan_session.plan_id is None:
        agent.plan_tree = None
        return
    if force_reload:
        try:
            agent.plan_tree = agent.plan_session.refresh()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to refresh plan tree: %s", exc)
            agent.plan_tree = None
    else:
        agent.plan_tree = agent.plan_session.current_tree()


def coerce_int(value: Any, field: str) -> int:
    if value is None:
        raise ValueError(f"{field} is missing or empty.")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise ValueError(f"{field} must be an integer; received {value!r}") from exc


def auto_decompose_plan(
    agent: Any,
    plan_id: int,
    *,
    wait_for_completion: bool = False,
    session_context: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    settings = agent.decomposer_settings
    if not settings.auto_on_create:
        note = "Automatic decomposition is disabled."
        if note not in agent._decomposition_notes:
            agent._decomposition_notes.append(note)
        return None
    if agent.plan_decomposer is None:
        note = "The automatic decomposer is not initialised."
        if note not in agent._decomposition_notes:
            agent._decomposition_notes.append(note)
        return None
    if settings.model is None:
        note = "Automatic decomposition was skipped: no decomposition model configured."
        if note not in agent._decomposition_notes:
            agent._decomposition_notes.append(note)
        return None
    if wait_for_completion:
        try:
            result = agent.plan_decomposer.run_plan(
                plan_id,
                max_depth=settings.max_depth,
                node_budget=settings.total_node_budget,
                session_context=session_context,
            )
        except Exception as exc:  # pragma: no cover - defensive
            message = f"Automatic decomposition failed: {exc}"
            logger.exception(
                "Auto decomposition failed for plan %s: %s", plan_id, exc
            )
            agent._decomposition_errors.append(message)
            return None
        agent._last_decomposition = result
        if result.created_tasks:
            agent._dirty = True
        try:
            refresh_plan_tree(agent, force_reload=True)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Failed to refresh plan tree after synchronous decomposition: %s",
                exc,
            )
            agent._decomposition_errors.append(
                f"Failed to refresh plan after decomposition: {exc}"
            )
        note = "Automatic decomposition completed synchronously."
        if note not in agent._decomposition_notes:
            agent._decomposition_notes.append(note)
        return {"result": result}
    try:
        job = start_decomposition_job_thread(
            agent.plan_decomposer,
            plan_id=plan_id,
            mode="plan_bfs",
            max_depth=settings.max_depth,
            node_budget=settings.total_node_budget,
        )
    except Exception as exc:  # pragma: no cover - defensive
        message = f"Failed to submit automatic task decomposition: {exc}"
        logger.exception(
            "Auto decomposition enqueue failed for plan %s: %s", plan_id, exc
        )
        agent._decomposition_errors.append(message)
        return None

    agent._last_decomposition = None
    note = "Automatic decomposition has been submitted for background execution."
    if note not in agent._decomposition_notes:
        agent._decomposition_notes.append(note)
    return {"job": job}


def persist_if_dirty(agent: Any) -> bool:
    if not agent._dirty or agent.plan_session.plan_id is None:
        return False
    note = f"session:{agent.session_id}" if agent.session_id else None
    refresh_plan_tree(agent, force_reload=True)
    agent.plan_session.persist_current_tree(note=note)
    agent._dirty = False
    return True
