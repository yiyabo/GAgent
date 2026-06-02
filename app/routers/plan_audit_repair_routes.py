from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.database import get_db
from app.repository.plan_repository import PlanRepository
from app.services.request_principal import ensure_owner_access, get_request_owner_id, require_authenticated_principal
from app.services.plans.audit_repair_loop import AuditRepairLoopConfig, AuditRepairLoopService
from app.services.plans.plan_executor import PlanExecutor
from app.services.plans.task_verification import TaskVerificationService

from . import register_router


audit_repair_router = APIRouter(prefix="/tasks", tags=["tasks"])

_plan_repo = PlanRepository()
_task_verifier = TaskVerificationService()
_plan_executor = PlanExecutor(repo=_plan_repo)
_audit_repair_loop_service = AuditRepairLoopService(
    repo=_plan_repo,
    verifier=_task_verifier,
    plan_executor=_plan_executor,
)


class AuditRepairTaskRequest(BaseModel):
    max_loops: int = Field(default=2, ge=1, le=5)
    max_task_repairs: int = Field(default=1, ge=0, le=3)
    enable_delegate_repair: bool = True
    enable_rerun: bool = True


class AuditRepairTaskResponse(BaseModel):
    success: bool
    message: str
    plan_id: int
    task_id: int
    final_status: str
    classification: str
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    result: Dict[str, Any] = Field(default_factory=dict)


def _ensure_plan_access(plan_id: int, request: Request) -> None:
    require_authenticated_principal(request)
    with get_db() as conn:
        row = conn.execute(
            "SELECT owner FROM plans WHERE id=?",
            (plan_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Plan {plan_id} not found")
    ensure_owner_access(request, row["owner"], detail="plan owner mismatch")


def _load_authorized_plan_tree(plan_id: int, request: Request):
    _ensure_plan_access(plan_id, request)
    try:
        return _plan_repo.get_plan_tree(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@audit_repair_router.post(
    "/{task_id}/audit-repair",
    response_model=AuditRepairTaskResponse,
    summary="Run explicit audit-repair loop for a task result",
)
def audit_repair_task_result(
    task_id: int,
    request: Request,
    plan_id: int = Query(..., description="plan ID"),
    payload: Optional[AuditRepairTaskRequest] = Body(default=None),
):
    payload = payload or AuditRepairTaskRequest()
    tree = _load_authorized_plan_tree(plan_id, request)
    if not tree.has_node(task_id):
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found in plan {plan_id}")
    node = tree.get_node(task_id)
    if not node.execution_result:
        raise HTTPException(
            status_code=400,
            detail=f"Task {task_id} has not produced an execution result yet; run it before audit-repair.",
        )
    try:
        result = _audit_repair_loop_service.run_task_loop(
            plan_id=plan_id,
            task_id=task_id,
            config=AuditRepairLoopConfig(
                max_loops=payload.max_loops,
                max_task_repairs=payload.max_task_repairs,
                enable_delegate_repair=payload.enable_delegate_repair,
                enable_rerun=payload.enable_rerun,
                session_id=None,
                owner_id=get_request_owner_id(request),
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    data = result.to_dict()
    return AuditRepairTaskResponse(
        success=result.success,
        message=result.message,
        plan_id=plan_id,
        task_id=task_id,
        final_status=result.final_status,
        classification=result.classification,
        steps=list(data.get("steps") or []),
        result=data,
    )


register_router(
    namespace="task_audit_repair",
    version="v1",
    path="/tasks",
    router=audit_repair_router,
    tags=["tasks"],
    description="Explicit task audit-repair loop APIs",
)
