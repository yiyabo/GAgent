"""Shared singletons, logger and execution locks for the plan/task routes."""

from __future__ import annotations

import logging
import threading
from typing import Dict, Optional, Tuple

from app.repository.plan_repository import PlanRepository
from app.services.plans.artifact_preflight import ArtifactPreflightService
from app.services.plans.audit_repair_loop import AuditRepairLoopService
from app.services.plans.plan_decomposer import PlanDecomposer
from app.services.plans.plan_executor import PlanExecutor
from app.services.plans.status_resolver import PlanStatusResolver
from app.services.plans.task_verification import TaskVerificationService

_plan_repo = PlanRepository()
_plan_decomposer = PlanDecomposer(repo=_plan_repo)
_plan_executor = PlanExecutor(repo=_plan_repo)
_task_verifier = TaskVerificationService()
_audit_repair_loop_service = AuditRepairLoopService(
    repo=_plan_repo,
    verifier=_task_verifier,
    plan_executor=_plan_executor,
)
_artifact_preflight_service = ArtifactPreflightService()
_plan_status_resolver = PlanStatusResolver()
# Spelled out (not __name__) so log records keep the "app.routers.plan_routes"
# logger name they had before the package split.
logger = logging.getLogger("app.routers.plan_routes")

# Guard against duplicate concurrent execution of the same plan+task pair.
_task_execution_locks: Dict[Tuple[int, int], threading.Lock] = {}
_task_execution_locks_guard = threading.Lock()


def _acquire_plan_execution_lock(plan_id: int, task_id: int = 0) -> Optional[threading.Lock]:
    """Acquire a non-blocking execution lock for a plan/task scope."""
    lock_key = (plan_id, task_id)
    with _task_execution_locks_guard:
        execution_lock = _task_execution_locks.get(lock_key)
        if execution_lock is None:
            execution_lock = threading.Lock()
            _task_execution_locks[lock_key] = execution_lock
        if not execution_lock.acquire(blocking=False):
            return None
        return execution_lock


def _release_plan_execution_lock(
    plan_id: int,
    task_id: int,
    execution_lock: threading.Lock,
) -> None:
    """Release and remove a previously acquired execution lock."""
    lock_key = (plan_id, task_id)
    with _task_execution_locks_guard:
        try:
            execution_lock.release()
        except RuntimeError:
            pass
        if _task_execution_locks.get(lock_key) is execution_lock:
            _task_execution_locks.pop(lock_key, None)
