"""Owner-scoped conversation quality analytics endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.repository import conversation_quality as repository
from app.routers import register_router
from app.services.request_principal import require_quality_analytics_access

router = APIRouter(prefix="/quality", tags=["quality"])

register_router(
    namespace="quality",
    version="v1",
    path="/quality",
    router=router,
    tags=["quality"],
    description="Evidence-based conversation quality analytics",
)


class QualityBreakdownItem(BaseModel):
    name: str
    count: int


class QualitySummaryResponse(BaseModel):
    total: int
    pending: int
    evaluated: int
    average_confidence: float
    by_satisfaction_level: List[QualityBreakdownItem]
    failure_modes: List[QualityBreakdownItem]
    responsible_stages: List[QualityBreakdownItem]
    request_tiers: List[QualityBreakdownItem]
    tools: List[QualityBreakdownItem]


class QualityCaseEvidence(BaseModel):
    source: str
    quote: str
    explanation: str


class QualityCaseSummary(BaseModel):
    id: int
    target_run_id: str
    session_id: str
    status: str
    evaluation_basis: Optional[str] = None
    satisfaction_level: Optional[str] = None
    confidence: Optional[float] = None
    failure_modes: List[str] = Field(default_factory=list)
    responsible_stages: List[str] = Field(default_factory=list)
    evidence: List[QualityCaseEvidence] = Field(default_factory=list)
    user_goal: str = ""
    created_at: Optional[str] = None
    evaluated_at: Optional[str] = None


def _since(hours: int) -> str:
    safe_hours = max(1, min(hours, 24 * 90))
    return (datetime.now(timezone.utc) - timedelta(hours=safe_hours)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _case_summary(row: Dict[str, Any]) -> QualityCaseSummary:
    result = row.get("evaluation") if isinstance(row.get("evaluation"), dict) else {}
    snapshot = row.get("snapshot") if isinstance(row.get("snapshot"), dict) else {}
    evidence = result.get("evidence") if isinstance(result.get("evidence"), list) else []
    return QualityCaseSummary(
        id=int(row["id"]),
        target_run_id=str(row["target_run_id"]),
        session_id=str(row["session_id"]),
        status=str(row["status"]),
        evaluation_basis=row.get("evaluation_basis"),
        satisfaction_level=row.get("satisfaction_level"),
        confidence=row.get("confidence"),
        failure_modes=list(result.get("failure_modes") or []),
        responsible_stages=list(result.get("responsible_stages") or []),
        evidence=evidence[:3],
        user_goal=str(snapshot.get("user_goal") or "")[:500],
        created_at=row.get("created_at"),
        evaluated_at=row.get("evaluated_at"),
    )


@router.get("/summary", response_model=QualitySummaryResponse)
async def get_quality_summary(
    request: Request,
    hours: int = Query(default=168, ge=1, le=2160),
) -> Dict[str, Any]:
    require_quality_analytics_access(request)
    return repository.get_quality_summary(since=_since(hours))


@router.get("/cases", response_model=List[QualityCaseSummary])
async def list_quality_cases(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: Optional[str] = Query(default=None),
    satisfaction_level: Optional[Literal["satisfied", "acceptable", "negative", "angry"]] = Query(default=None),
    failure_mode: Optional[str] = Query(default=None, max_length=80),
    hours: int = Query(default=168, ge=1, le=2160),
) -> List[QualityCaseSummary]:
    require_quality_analytics_access(request)
    rows = repository.list_evaluations(
        limit=limit,
        offset=offset,
        status=status,
        satisfaction_level=satisfaction_level,
        failure_mode=failure_mode,
        since=_since(hours),
    )
    return [_case_summary(row) for row in rows]


@router.get("/cases/{evaluation_id}")
async def get_quality_case(evaluation_id: int, request: Request) -> Dict[str, Any]:
    require_quality_analytics_access(request)
    row = repository.get_evaluation(evaluation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="quality evaluation not found")
    return {
        "id": row["id"],
        "target_run_id": row["target_run_id"],
        "session_id": row["session_id"],
        "status": row["status"],
        "evaluation_basis": row.get("evaluation_basis"),
        "satisfaction_level": row.get("satisfaction_level"),
        "confidence": row.get("confidence"),
        "label_source": row.get("label_source"),
        "evaluation": row.get("evaluation"),
        "snapshot": row.get("snapshot"),
        "evaluator_provider": row.get("evaluator_provider"),
        "evaluator_model": row.get("evaluator_model"),
        "prompt_version": row.get("prompt_version"),
        "created_at": row.get("created_at"),
        "evaluated_at": row.get("evaluated_at"),
        "finalized_at": row.get("finalized_at"),
    }
