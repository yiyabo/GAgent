from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.repository.plan_repository import PlanRepository
from app.services.plans.plan_decomposer import PlanDecomposer, DecompositionResult
from app.services.plans.decomposition_jobs import (
    execute_decomposition_job,
    plan_decomposition_jobs,
)
from . import register_router

plan_router = APIRouter(prefix="/plans", tags=["plans"])
task_router = APIRouter(prefix="/tasks", tags=["tasks"])

_plan_repo = PlanRepository()
_plan_decomposer = PlanDecomposer(repo=_plan_repo)
logger = logging.getLogger(__name__)


def _sse_message(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _run_decomposition_job(
    job_id: str,
    plan_id: int,
    task_id: Optional[int],
    expand_depth: Optional[int],
    node_budget: Optional[int],
    allow_existing_children: Optional[bool],
) -> None:
    """Background execution wrapper for async decomposition jobs."""
    execute_decomposition_job(
        plan_decomposer=_plan_decomposer,
        job_id=job_id,
        plan_id=plan_id,
        mode="single_node",
        task_id=task_id,
        expand_depth=expand_depth,
        node_budget=node_budget,
        allow_existing_children=allow_existing_children,
    )


@plan_router.get("", summary="列出计划概览")
def list_plans():
    """Return plan summaries."""
    summaries = _plan_repo.list_plans()
    return [summary.model_dump() for summary in summaries]


class SubgraphResponse(BaseModel):
    plan_id: int
    root_node: int
    max_depth: int
    outline: str
    nodes: list[dict[str, Any]]


class DecomposeTaskRequest(BaseModel):
    plan_id: int = Field(..., description="计划 ID，必填")
    expand_depth: Optional[int] = Field(None, ge=1, description="扩展深度（默认为配置值）")
    node_budget: Optional[int] = Field(None, ge=1, description="此次分解允许创建的最大节点数")
    allow_existing_children: Optional[bool] = Field(
        None, description="是否允许在已有子任务的节点上继续追加子任务"
    )
    async_mode: bool = Field(
        False,
        description="是否使用异步执行模式（立即返回 job_id，并在后台完成分解）",
    )


class DecomposeTaskResponse(BaseModel):
    success: bool
    message: str
    result: Dict[str, Any]
    job: Optional[Dict[str, Any]] = Field(
        default=None, description="异步模式下的任务状态信息"
    )


class DecompositionJobStatusResponse(BaseModel):
    job_id: str
    job_type: str = "plan_decompose"
    status: str
    plan_id: Optional[int] = None
    task_id: Optional[int] = None
    mode: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    stats: Dict[str, Any] = Field(default_factory=dict)
    params: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    logs: List[Dict[str, Any]] = Field(default_factory=list)


class TaskResultItem(BaseModel):
    task_id: int
    name: Optional[str] = None
    status: Optional[str] = None
    content: Optional[str] = None
    notes: List[str] = []
    metadata: Dict[str, Any] = {}
    raw: Optional[Dict[str, Any]] = None


class PlanResultsResponse(BaseModel):
    plan_id: int
    total: int
    items: List[TaskResultItem]


class PlanExecutionSummary(BaseModel):
    plan_id: int
    total_tasks: int
    completed: int
    failed: int
    skipped: int
    running: int
    pending: int


def _parse_execution_result(raw_value: Any) -> Tuple[Optional[str], List[str], Dict[str, Any], Optional[Dict[str, Any]]]:
    """Normalize execution result payloads into structured components."""

    if raw_value in (None, ""):
        return None, [], {}, None

    payload: Any = raw_value
    if isinstance(raw_value, (bytes, bytearray)):
        try:
            payload = raw_value.decode("utf-8")
        except Exception:  # pragma: no cover - defensive
            payload = raw_value

    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            # legacy plain-text payload
            return payload, [], {}, None

    if isinstance(payload, dict):
        content = payload.get("content")
        notes_data = payload.get("notes") or []
        if isinstance(notes_data, list):
            notes = [str(item) for item in notes_data if item is not None]
        else:
            notes = [str(notes_data)]
        metadata = payload.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        return content, notes, metadata, payload

    # Fallback for unexpected payload types
    return str(payload), [], {}, None


@plan_router.get("/{plan_id}/tree", summary="获取完整计划树")
def get_plan_tree(plan_id: int):
    """Return serialized PlanTree for the specified plan."""
    try:
        tree = _plan_repo.get_plan_tree(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return tree.model_dump()


@plan_router.get(
    "/{plan_id}/results",
    response_model=PlanResultsResponse,
    summary="获取计划内所有任务的执行输出（最新）",
)
def get_plan_results(
    plan_id: int,
    only_with_output: bool = Query(True, description="仅返回包含执行结果的任务"),
):
    try:
        tree = _plan_repo.get_plan_tree(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    items: List[TaskResultItem] = []
    for node in tree.ordered_nodes():
        content, notes, metadata, raw_payload = _parse_execution_result(node.execution_result)
        if content is None and not notes and not metadata and only_with_output:
            continue
        items.append(
            TaskResultItem(
                task_id=node.id,
                name=node.name,
                status=node.status,
                content=content,
                notes=notes,
                metadata=metadata,
                raw=raw_payload,
            )
        )

    return PlanResultsResponse(plan_id=plan_id, total=len(items), items=items)


@task_router.get(
    "/{task_id}/result",
    response_model=TaskResultItem,
    summary="获取单个任务的执行输出（最新）",
)
def get_task_result(task_id: int, plan_id: int = Query(..., description="计划 ID")):
    try:
        tree = _plan_repo.get_plan_tree(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not tree.has_node(task_id):
        raise HTTPException(status_code=404, detail=f"Plan {plan_id} 中未找到节点 {task_id}")
    node = tree.get_node(task_id)

    content, notes, metadata, raw_payload = _parse_execution_result(node.execution_result)

    return TaskResultItem(
        task_id=node.id,
        name=node.name,
        status=node.status,
        content=content,
        notes=notes,
        metadata=metadata,
        raw=raw_payload,
    )


@plan_router.get(
    "/{plan_id}/execution/summary",
    response_model=PlanExecutionSummary,
    summary="根据当前任务状态聚合执行统计",
)
def get_plan_execution_summary(plan_id: int):
    try:
        tree = _plan_repo.get_plan_tree(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    total = tree.node_count()
    status_counts = {"completed": 0, "failed": 0, "skipped": 0, "running": 0, "pending": 0}
    for node in tree.nodes.values():
        st = (node.status or "pending").lower()
        if st in status_counts:
            status_counts[st] += 1
        else:
            status_counts["pending"] += 1
    return PlanExecutionSummary(
        plan_id=plan_id,
        total_tasks=total,
        completed=status_counts["completed"],
        failed=status_counts["failed"],
        skipped=status_counts["skipped"],
        running=status_counts["running"],
        pending=status_counts["pending"],
    )


@plan_router.get(
    "/{plan_id}/subgraph",
    response_model=SubgraphResponse,
    summary="获取计划子图",
)
def get_plan_subgraph(
    plan_id: int,
    node_id: int = Query(..., description="子图根节点 ID"),
    max_depth: int = Query(2, ge=1, le=6, description="递归深度限制"),
):
    try:
        tree = _plan_repo.get_plan_tree(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not tree.has_node(node_id):
        raise HTTPException(
            status_code=404,
            detail=f"Plan {plan_id} 中未找到节点 {node_id}",
        )
    nodes = tree.subgraph_nodes(node_id, max_depth=max_depth)
    outline = tree.subgraph_outline(node_id, max_depth=max_depth)
    return SubgraphResponse(
        plan_id=plan_id,
        root_node=node_id,
        max_depth=max_depth,
        outline=outline,
        nodes=[node.model_dump() for node in nodes],
    )


@task_router.post(
    "/{task_id}/decompose",
    response_model=DecomposeTaskResponse,
    summary="对指定任务执行 LLM 分解",
)
def decompose_task(
    task_id: int,
    background_tasks: BackgroundTasks,
    request: DecomposeTaskRequest = Body(...),
):
    plan_id = request.plan_id
    try:
        tree = _plan_repo.get_plan_tree(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not tree.has_node(task_id):
        raise HTTPException(
            status_code=404,
            detail=f"Plan {plan_id} 中未找到节点 {task_id}",
        )

    expand_depth = request.expand_depth
    node_budget = request.node_budget
    allow_existing_children = request.allow_existing_children

    if request.async_mode:
        job = plan_decomposition_jobs.create_job(
            plan_id=plan_id,
            task_id=task_id,
            mode="single_node",
            params={
                "expand_depth": expand_depth,
                "node_budget": node_budget,
                "allow_existing_children": allow_existing_children,
            },
        )
        if background_tasks is None:
            raise HTTPException(
                status_code=500, detail="后台任务管理不可用，无法执行异步分解。"
            )
        plan_decomposition_jobs.append_log(
            job.job_id,
            "info",
            "任务拆分已进入后台队列",
            {
                "plan_id": plan_id,
                "task_id": task_id,
                "expand_depth": expand_depth,
                "node_budget": node_budget,
                "allow_existing_children": allow_existing_children,
            },
        )
        background_tasks.add_task(
            _run_decomposition_job,
            job.job_id,
            plan_id,
            task_id,
            expand_depth,
            node_budget,
            allow_existing_children,
        )
        message = (
            "任务拆分已提交到后台执行。你可以稍后查询 job 状态或刷新计划树查看进度。"
        )
        payload = job.to_payload()
        return DecomposeTaskResponse(
            success=True,
            message=message,
            result={"job_id": job.job_id, "status": job.status},
            job=payload,
        )

    try:
        result: DecompositionResult = _plan_decomposer.decompose_node(
            plan_id,
            task_id,
            expand_depth=expand_depth,
            node_budget=node_budget,
            allow_existing_children=allow_existing_children,
        )
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    message = (
        f"已生成 {len(result.created_tasks)} 个子任务。"
        if result.created_tasks
        else "分解完成，未新增任务。"
    )
    if result.stopped_reason:
        message += f" 停止原因：{result.stopped_reason}"

    return DecomposeTaskResponse(
        success=True,
        message=message,
        result=result.model_dump(),
        job=None,
    )


@task_router.get(
    "/decompose/jobs/{job_id}/stream",
    summary="实时订阅异步拆分日志",
)
async def stream_decomposition_job(job_id: str):
    snapshot = plan_decomposition_jobs.get_job_payload(job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="未找到对应的拆分任务。")

    loop = asyncio.get_running_loop()
    queue = plan_decomposition_jobs.register_subscriber(job_id, loop)
    if queue is None:
        async def snapshot_only() -> AsyncIterator[str]:
            yield _sse_message({"type": "snapshot", "job": snapshot})

        headers = {
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
        return StreamingResponse(snapshot_only(), media_type="text/event-stream", headers=headers)

    async def event_generator() -> AsyncIterator[str]:
        try:
            yield _sse_message({"type": "snapshot", "job": snapshot})
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    heartbeat = plan_decomposition_jobs.get_job_payload(job_id, include_logs=False)
                    if heartbeat is None:
                        break
                    yield _sse_message({"type": "heartbeat", "job": heartbeat})
                    continue
                message.setdefault("type", "event")
                yield _sse_message(message)
                if message.get("status") in {"succeeded", "failed"}:
                    break
        except asyncio.CancelledError:  # pragma: no cover - defensive
            raise
        finally:
            plan_decomposition_jobs.unregister_subscriber(job_id, queue)

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)


@task_router.get(
    "/decompose/jobs/{job_id}",
    response_model=DecompositionJobStatusResponse,
    summary="查询异步任务拆分状态",
)
def get_decomposition_job_status(job_id: str):
    payload = plan_decomposition_jobs.get_job_payload(job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="未找到对应的拆分任务。")
    return DecompositionJobStatusResponse(
        job_id=payload.get("job_id"),
        job_type=payload.get("job_type") or "plan_decompose",
        status=payload.get("status"),
        plan_id=payload.get("plan_id"),
        task_id=payload.get("task_id"),
        mode=payload.get("mode"),
        result=payload.get("result"),
        stats=payload.get("stats") or {},
        params=payload.get("params") or {},
        metadata=payload.get("metadata") or {},
        error=payload.get("error"),
        created_at=payload.get("created_at"),
        started_at=payload.get("started_at"),
        finished_at=payload.get("finished_at"),
        logs=payload.get("logs", []),
    )


register_router(
    namespace="plans",
    version="v1",
    path="/plans",
    router=plan_router,
    tags=["plans"],
    description="计划树读取接口",
)

register_router(
    namespace="tasks",
    version="v1",
    path="/tasks",
    router=task_router,
    tags=["tasks"],
    description="面向外部的任务 REST 功能（PlanTree 补充接口）",
)
