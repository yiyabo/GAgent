"""
Batch Executor: one-click batch execution with concurrency and rate limiting

Scope:
- Execute all pending ATOMIC tasks under a given ROOT or COMPOSITE task.
- Concurrency control via asyncio.Semaphore.
- Lightweight rate limiting by spacing task starts (token-less simple scheduler).
- Retries with exponential backoff and jitter for robustness (real API only).
- Relies on ToolEnhancedExecutor for pure LLM semantic tool routing and materialization.

Usage (programmatic):
- from app.execution.orchestrators.batch_executor import BatchExecutor
- await BatchExecutor(repo).run(parent_id=<root_or_composite_id>, concurrency=4, rate_limit_per_minute=12)

Notes:
- Assembly (COMPOSITE/ROOT summary.md) is triggered automatically by ToolEnhancedExecutor after each ATOMIC completion.
- The finalization step is idempotent; optionally re-assembles the root at the end when finalize=True.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Dict, List, Optional, Tuple

from ...interfaces import TaskRepository
from ...repository.tasks import default_repo
from ..executors.tool_enhanced import (
    ToolEnhancedExecutor,
    create_tool_enhanced_context_options,
)
from ..assemblers import RootAssembler

logger = logging.getLogger(__name__)


PENDING_STATUSES = {"pending", "in_progress", "created", "queued"}
DONE_STATUSES = {"done", "completed"}
RETRYABLE_STATUSES = {"failed", "error"}


class BatchExecutor:
    """Batch execution orchestrator with concurrency and rate control."""

    def __init__(self, repo: Optional[TaskRepository] = None):
        self.repo = repo or default_repo
        self.executor: Optional[ToolEnhancedExecutor] = None

    async def run(
        self,
        parent_id: int,
        concurrency: int = 4,
        rate_limit_per_minute: int = 12,
        max_retries: int = 2,
        finalize: bool = True,
        context_options: Optional[Dict[str, Any]] = None,
        include_retryables: bool = True,
    ) -> Dict[str, Any]:
        """
        Execute all pending ATOMIC tasks under the given ROOT/COMPOSITE parent.

        Args:
            parent_id: ROOT or COMPOSITE task id
            concurrency: max concurrent ATOMIC executions
            rate_limit_per_minute: soft cap for task start rate (spacing between starts)
            max_retries: retry times per ATOMIC on non-success status
            finalize: whether to attempt a final root assembly (idempotent)
            context_options: optional context options (will be merged with tool-enhanced defaults)
            include_retryables: whether to include tasks with retryable statuses (failed/error)

        Returns:
            Summary dict with counts and per-task results
        """
        t0 = time.time()
        parent = self.repo.get_task_info(parent_id)
        if not parent:
            return {"success": False, "error": f"Parent task {parent_id} not found"}

        parent_type = parent.get("task_type")
        if parent_type not in {"root", "composite"}:
            return {"success": False, "error": f"Parent {parent_id} must be root or composite, got {parent_type}"}

        atomic_tasks = self._collect_atomic_tasks(parent_id, include_retryables=include_retryables)
        if not atomic_tasks:
            return {
                "success": True,
                "message": "No pending atomic tasks under parent",
                "parent_id": parent_id,
                "duration_sec": round(time.time() - t0, 3),
                "executed": 0,
                "succeeded": 0,
                "failed": 0,
                "results": [],
            }

        logger.info(
            "Batch start: parent=%s (%s), atomic_count=%d, concurrency=%d, rate_limit_per_min=%d",
            parent_id,
            parent_type,
            len(atomic_tasks),
            concurrency,
            rate_limit_per_minute,
        )

        # Prepare executor and context options
        self.executor = ToolEnhancedExecutor(self.repo)
        await self.executor.initialize()
        ctx_opts = create_tool_enhanced_context_options(context_options or {})

        sem = asyncio.Semaphore(max(1, int(concurrency)))
        spacing = 60.0 / float(rate_limit_per_minute) if rate_limit_per_minute and rate_limit_per_minute > 0 else 0.0

        tasks: List[asyncio.Task] = []
        for idx, t in enumerate(atomic_tasks):
            initial_delay = idx * spacing if spacing > 0 else 0.0
            coro = self._run_one(t, sem, initial_delay, max_retries, ctx_opts)
            tasks.append(asyncio.create_task(coro))

        results: List[Dict[str, Any]] = await asyncio.gather(*tasks, return_exceptions=False)

        succeeded = sum(1 for r in results if r.get("status") in ("done", "completed"))
        failed = len(results) - succeeded

        # Optional finalization: attempt root assembly (idempotent)
        if finalize:
            try:
                root_id = self._find_root_id(parent_id)
                if root_id:
                    RootAssembler(self.repo).assemble(root_id, strategy="llm", force_real=True)
                    logger.info("Finalized root assembly for %s", root_id)
            except Exception as e:
                logger.warning("Finalization assemble failed: %s", e)

        dt = round(time.time() - t0, 3)
        summary = {
            "success": True,
            "parent_id": parent_id,
            "parent_type": parent_type,
            "duration_sec": dt,
            "executed": len(results),
            "succeeded": succeeded,
            "failed": failed,
            "results": results,
        }
        logger.info("Batch finished: %s", summary)
        return summary

    def _collect_atomic_tasks(self, parent_id: int, include_retryables: bool = True) -> List[Dict[str, Any]]:
        """BFS collect all pending atomic tasks under a ROOT/COMPOSITE parent."""
        queue: List[int] = [parent_id]
        seen: set[int] = set()
        atomics: List[Dict[str, Any]] = []

        while queue:
            cur = queue.pop(0)
            if cur in seen:
                continue
            seen.add(cur)

            children = self.repo.get_children(cur) or []
            for ch in children:
                ttype = ch.get("task_type")
                status = (ch.get("status") or "").lower()
                if ttype == "atomic":
                    if (status in PENDING_STATUSES) or (include_retryables and status in RETRYABLE_STATUSES):
                        atomics.append(ch)
                elif ttype in {"composite", "root"}:
                    queue.append(ch.get("id"))
                else:
                    # Unknown type, ignore
                    pass

        return atomics

    async def _run_one(
        self,
        task: Dict[str, Any],
        sem: asyncio.Semaphore,
        initial_delay: float,
        max_retries: int,
        context_options: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run a single ATOMIC with concurrency guard, start spacing, and retries."""
        task_id = task.get("id")
        task_name = task.get("name")

        if initial_delay > 0:
            await asyncio.sleep(initial_delay)

        async with sem:
            attempt = 0
            while True:
                try:
                    status = await self.executor.execute_task(
                        task,
                        use_context=True,
                        context_options=context_options,
                    )
                    if status in ("done", "completed"):
                        return {"id": task_id, "name": task_name, "status": status}
                    else:
                        # Treat non-success as retryable once, then fail
                        if attempt >= max_retries:
                            return {"id": task_id, "name": task_name, "status": status, "error": "non_success"}
                except Exception as e:
                    # Executor-level error: retry with backoff
                    if attempt >= max_retries:
                        return {"id": task_id, "name": task_name, "status": "failed", "error": str(e)}
                # Backoff and retry
                delay = min(20.0, (2 ** attempt) + random.uniform(0, 0.5))
                await asyncio.sleep(delay)
                attempt += 1

    def _find_root_id(self, task_id: int) -> Optional[int]:
        """Walk up the parent chain to find root id."""
        cur = self.repo.get_task_info(task_id)
        guard = 0
        while cur and guard < 100:
            if cur.get("task_type") == "root":
                return cur.get("id")
            parent = self.repo.get_parent(cur.get("id"))
            cur = parent
            guard += 1
        return None


# Synchronous wrapper for convenience

def run_batch(
    parent_id: int,
    repo: Optional[TaskRepository] = None,
    concurrency: int = 4,
    rate_limit_per_minute: int = 12,
    max_retries: int = 2,
    finalize: bool = True,
    context_options: Optional[Dict[str, Any]] = None,
    include_retryables: bool = True,
) -> Dict[str, Any]:
    """Run batch execution from sync context by spinning an event loop."""
    async def _runner():
        return await BatchExecutor(repo).run(
            parent_id=parent_id,
            concurrency=concurrency,
            rate_limit_per_minute=rate_limit_per_minute,
            max_retries=max_retries,
            finalize=finalize,
            context_options=context_options,
            include_retryables=include_retryables,
        )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        # If we're in an async context already, require caller to await the async API instead.
        raise RuntimeError("run_batch() called in running event loop; use BatchExecutor(repo).run(...) instead.")
    return asyncio.run(_runner())
