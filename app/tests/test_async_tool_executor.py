"""Tests for Phase 2 — AsyncToolExecutor concurrent execution.

Validates that:
1. Concurrent-safe tools run in parallel (timing-based proof)
2. Non-safe tools run sequentially
3. Mixed batches: safe tools parallel first, then unsafe sequential
4. Results returned in original submission order
5. Exceptions in one tool don't crash others
6. classify_tool_concurrency respects registry metadata
"""

from __future__ import annotations

import asyncio
import time
import pytest

from app.services.execution.async_tool_executor import (
    PendingToolCall,
    classify_tool_concurrency,
    execute_with_concurrency,
)
from tool_box.tools import get_tool_registry, register_tool
from tool_box.tool_registry import register_all_tools


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _fresh_registry():
    registry = get_tool_registry()
    for name in list(registry.tools.keys()):
        registry.unregister_tool(name)
    yield
    for name in list(registry.tools.keys()):
        registry.unregister_tool(name)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_call(
    index: int,
    name: str,
    *,
    concurrent_safe: bool,
    delay: float = 0.0,
    result: dict | None = None,
    raise_exc: Exception | None = None,
) -> PendingToolCall:
    async def _factory():
        if delay > 0:
            await asyncio.sleep(delay)
        if raise_exc is not None:
            raise raise_exc
        return result or {"tool": name, "index": index, "success": True}

    return PendingToolCall(
        index=index,
        tool_name=name,
        coroutine_factory=_factory,
        is_concurrent_safe=concurrent_safe,
    )


# ---------------------------------------------------------------------------
# Core execution tests
# ---------------------------------------------------------------------------

class TestExecuteWithConcurrency:
    @pytest.mark.asyncio
    async def test_empty_list_returns_empty(self):
        results = await execute_with_concurrency([])
        assert results == []

    @pytest.mark.asyncio
    async def test_single_call_returns_result(self):
        calls = [_make_call(0, "web_search", concurrent_safe=True)]
        results = await execute_with_concurrency(calls)
        assert len(results) == 1
        assert results[0]["tool"] == "web_search"

    @pytest.mark.asyncio
    async def test_concurrent_safe_tools_run_in_parallel(self):
        """Three concurrent-safe tools each sleeping 0.1s should complete in ~0.1s, not 0.3s."""
        calls = [
            _make_call(i, f"search_{i}", concurrent_safe=True, delay=0.1)
            for i in range(3)
        ]
        t0 = time.monotonic()
        results = await execute_with_concurrency(calls)
        elapsed = time.monotonic() - t0

        assert len(results) == 3
        assert all(r["success"] for r in results)
        # Parallel: should be ~0.1s. Sequential would be ~0.3s.
        assert elapsed < 0.25, f"Expected parallel execution, but took {elapsed:.2f}s"

    @pytest.mark.asyncio
    async def test_non_safe_tools_run_sequentially(self):
        """Two non-safe tools each sleeping 0.1s should take ~0.2s (sequential)."""
        calls = [
            _make_call(0, "code_executor", concurrent_safe=False, delay=0.1),
            _make_call(1, "phagescope", concurrent_safe=False, delay=0.1),
        ]
        t0 = time.monotonic()
        results = await execute_with_concurrency(calls)
        elapsed = time.monotonic() - t0

        assert len(results) == 2
        assert all(r["success"] for r in results)
        # Sequential: should be ~0.2s
        assert elapsed >= 0.18, f"Expected sequential execution, but took {elapsed:.2f}s"

    @pytest.mark.asyncio
    async def test_mixed_batch_safe_first_then_unsafe(self):
        """Safe tools run in parallel, then unsafe tools run sequentially."""
        execution_order = []

        async def make_factory(name, delay, safe):
            async def _f():
                execution_order.append(f"start:{name}")
                await asyncio.sleep(delay)
                execution_order.append(f"end:{name}")
                return {"tool": name, "success": True}
            return _f

        calls = [
            PendingToolCall(0, "web_search", await make_factory("web_search", 0.05, True), True),
            PendingToolCall(1, "code_exec", await make_factory("code_exec", 0.05, False), False),
            PendingToolCall(2, "grep", await make_factory("grep", 0.05, True), True),
        ]
        results = await execute_with_concurrency(calls)

        assert len(results) == 3
        # Safe tools (web_search, grep) should start before code_exec ends
        ws_start = execution_order.index("start:web_search")
        grep_start = execution_order.index("start:grep")
        code_start = execution_order.index("start:code_exec")
        # Both safe tools should start before the unsafe one
        assert ws_start < code_start
        assert grep_start < code_start

    @pytest.mark.asyncio
    async def test_results_returned_in_original_order(self):
        """Results are sorted by index regardless of completion order."""
        calls = [
            _make_call(0, "slow", concurrent_safe=True, delay=0.1,
                       result={"name": "slow", "success": True}),
            _make_call(1, "fast", concurrent_safe=True, delay=0.01,
                       result={"name": "fast", "success": True}),
        ]
        results = await execute_with_concurrency(calls)
        assert results[0]["name"] == "slow"
        assert results[1]["name"] == "fast"

    @pytest.mark.asyncio
    async def test_exception_in_one_tool_doesnt_crash_others(self):
        calls = [
            _make_call(0, "good", concurrent_safe=True, delay=0.01),
            _make_call(1, "bad", concurrent_safe=True,
                       raise_exc=RuntimeError("boom")),
            _make_call(2, "good2", concurrent_safe=True, delay=0.01),
        ]
        results = await execute_with_concurrency(calls)
        assert results[0]["success"] is True
        assert results[1]["success"] is False
        assert "boom" in results[1]["error"]
        assert results[2]["success"] is True

    @pytest.mark.asyncio
    async def test_exception_in_sequential_tool(self):
        calls = [
            _make_call(0, "safe", concurrent_safe=True, delay=0.01),
            _make_call(1, "bad_unsafe", concurrent_safe=False,
                       raise_exc=ValueError("fail")),
        ]
        results = await execute_with_concurrency(calls)
        assert results[0]["success"] is True
        assert results[1]["success"] is False
        assert "fail" in results[1]["error"]


# ---------------------------------------------------------------------------
# classify_tool_concurrency
# ---------------------------------------------------------------------------

class TestClassifyToolConcurrency:
    def test_unregistered_tool_returns_false(self):
        assert classify_tool_concurrency("nonexistent_tool") is False

    def test_registered_concurrent_safe_tool(self):
        register_all_tools()
        assert classify_tool_concurrency("web_search") is True
        assert classify_tool_concurrency("database_query") is True

    def test_registered_non_concurrent_tool(self):
        register_all_tools()
        assert classify_tool_concurrency("code_executor") is False
        assert classify_tool_concurrency("terminal_session") is False
