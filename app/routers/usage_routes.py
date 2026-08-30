"""LLM token/cost attribution API.

Answers "where did the tokens go": platform-wide or per-session overview,
top-consumer sessions, per-run (per conversation turn) breakdown, and raw
per-call drill-down for root-causing an expensive session or turn.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from . import register_router

router = APIRouter(prefix="/usage", tags=["usage"])

register_router(
    namespace="usage",
    version="v1",
    path="/usage",
    router=router,
    tags=["usage"],
    description="LLM token/cost attribution: overview, top sessions, per-run and per-call drill-down",
)


@router.get("/overview", summary="Aggregate token/cost totals with breakdowns")
async def get_usage_overview_endpoint(
    hours: Optional[int] = Query(default=None, description="Look back N hours (alternative to start/end)"),
    session_id: Optional[str] = Query(default=None),
    start: Optional[str] = Query(default=None, description="ISO timestamp lower bound (created_at)"),
    end: Optional[str] = Query(default=None, description="ISO timestamp upper bound (created_at)"),
):
    try:
        from ..repository.llm_usage import get_usage_overview
        return get_usage_overview(hours=hours, session_id=session_id, start=start, end=end)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to get usage overview: {exc}")


@router.get("/top/sessions", summary="Rank sessions by token consumption")
async def get_top_sessions_endpoint(
    limit: int = Query(default=10, ge=1, le=100),
    hours: Optional[int] = Query(default=None),
):
    try:
        from ..repository.llm_usage import get_top_sessions
        return {"sessions": get_top_sessions(limit=limit, hours=hours)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to get top sessions: {exc}")


@router.get("/runs", summary="Per-run (per conversation turn) breakdown for a session")
async def get_session_runs_endpoint(
    session_id: str = Query(...),
    limit: int = Query(default=20, ge=1, le=200),
    hours: Optional[int] = Query(default=None),
):
    try:
        from ..repository.llm_usage import get_session_run_breakdown
        return {
            "session_id": session_id,
            "runs": get_session_run_breakdown(session_id, limit=limit, hours=hours),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to get run breakdown: {exc}")


@router.get("/calls", summary="Raw per-call drill-down")
async def get_usage_calls_endpoint(
    session_id: Optional[str] = Query(default=None),
    run_id: Optional[str] = Query(default=None),
    phase: Optional[str] = Query(default=None, description="chat/plan/execution/audit/..."),
    purpose: Optional[str] = Query(default=None, description="call_purpose filter"),
    status: Optional[str] = Query(default=None, description="ok/error filter"),
    limit: int = Query(default=200, ge=1, le=1000),
):
    if not any([session_id, run_id, phase, purpose, status]):
        raise HTTPException(
            status_code=422,
            detail="Provide at least one filter (session_id/run_id/phase/purpose/status)",
        )
    try:
        from ..repository.llm_usage import get_usage_calls
        return {"calls": get_usage_calls(
            session_id=session_id, run_id=run_id, phase=phase,
            purpose=purpose, status=status, limit=limit,
        )}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to get usage calls: {exc}")
