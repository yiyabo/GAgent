"""Legacy decomposition routes.

These endpoints were built on the old ``app.services.planning`` stack that
targeted the retired task-table workflow. The main FastAPI app no longer
registers this router; the retained module exists only to fail closed with a
clear migration message for any out-of-band imports or ad-hoc local use.
"""

from fastapi import APIRouter, Body, HTTPException
from typing import Any, Dict

router = APIRouter(prefix="/tasks", tags=["decomposition"])


_LEGACY_DECOMPOSITION_DETAIL = (
    "Legacy decomposition routes have been retired. Use the PlanTree-backed "
    "planning stack instead: /tasks/{task_id}/decompose from plan_routes for "
    "task decomposition, /plans/{plan_id}/tree for plan state, or the chat "
    "JSON-action pipeline for interactive decomposition."
)


def _raise_legacy_decomposition_retired() -> None:
    raise HTTPException(status_code=410, detail=_LEGACY_DECOMPOSITION_DETAIL)


@router.post("/{task_id}/decompose")
def decompose_task_endpoint(task_id: int, payload: Dict[str, Any] = Body(default={})):
    """Retired legacy endpoint retained only for explicit guidance."""
    _raise_legacy_decomposition_retired()


@router.post("/{task_id}/decompose/tool-aware")
async def decompose_task_tool_aware_endpoint(task_id: int, payload: Dict[str, Any] = Body(default={})):
    """Retired legacy endpoint retained only for explicit guidance."""
    _raise_legacy_decomposition_retired()


@router.get("/{task_id}/complexity")
def evaluate_task_complexity_endpoint(task_id: int):
    """Retired legacy endpoint retained only for explicit guidance."""
    _raise_legacy_decomposition_retired()


@router.post("/{task_id}/decompose/with-evaluation")
def decompose_task_with_evaluation_endpoint(task_id: int, payload: Dict[str, Any] = Body(default={})):
    """Retired legacy endpoint retained only for explicit guidance."""
    _raise_legacy_decomposition_retired()


@router.get("/{task_id}/decomposition/recommendation")
def get_decomposition_recommendation(task_id: int, min_complexity_score: float = 0.6):
    """Retired legacy endpoint retained only for explicit guidance."""
    _raise_legacy_decomposition_retired()


@router.post("/plans/{title}/decompose")
def decompose_plan_endpoint(title: str, payload: Dict[str, Any] = Body(default={})):
    """Retired legacy endpoint retained only for explicit guidance."""
    _raise_legacy_decomposition_retired()
