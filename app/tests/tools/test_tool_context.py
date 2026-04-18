"""Tests for Phase 1.2 — ToolContext injection.

Validates that:
1. ToolContext is constructed with correct defaults
2. Handlers that declare ``tool_context`` receive it
3. Handlers that do NOT declare ``tool_context`` are unaffected
4. ToolContext flows through the full call chain (execute_tool → call_tool → handler)
5. UnifiedToolExecutor builds and injects ToolContext from ToolExecutionContext
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from tool_box.context import ToolContext
from tool_box.tools import register_tool, get_tool_registry
from tool_box.integration import ToolBoxIntegration


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
# ToolContext dataclass
# ---------------------------------------------------------------------------

class TestToolContextDefaults:
    def test_bare_context_has_safe_defaults(self):
        ctx = ToolContext()
        assert ctx.session_id is None
        assert ctx.plan_id is None
        assert ctx.task_id is None
        assert ctx.work_dir == ""
        assert ctx.capability_floor == "tools"
        assert ctx.tool_history == []
        assert ctx.abort_event is None
        assert ctx.on_progress is None
        assert ctx.extra == {}

    def test_is_cancelled_false_without_event(self):
        ctx = ToolContext()
        assert ctx.is_cancelled is False

    def test_is_cancelled_false_when_event_not_set(self):
        event = asyncio.Event()
        ctx = ToolContext(abort_event=event)
        assert ctx.is_cancelled is False

    def test_is_cancelled_true_when_event_set(self):
        event = asyncio.Event()
        event.set()
        ctx = ToolContext(abort_event=event)
        assert ctx.is_cancelled is True


# ---------------------------------------------------------------------------
# Injection through call_tool
# ---------------------------------------------------------------------------

class TestToolContextInjection:
    @pytest.fixture()
    def _integration(self):
        return ToolBoxIntegration()

    def test_context_aware_handler_receives_context(self, _integration):
        received = {}

        async def handler(query: str, tool_context: ToolContext = None):
            received["ctx"] = tool_context
            return {"ok": True}

        register_tool(
            name="ctx_test",
            description="test",
            category="test",
            parameters_schema={"type": "object"},
            handler=handler,
        )

        ctx = ToolContext(session_id="s1", plan_id=7)
        asyncio.run(_integration.call_tool("ctx_test", tool_context=ctx, query="hi"))

        assert received["ctx"] is ctx
        assert received["ctx"].session_id == "s1"
        assert received["ctx"].plan_id == 7

    def test_context_unaware_handler_not_affected(self, _integration):
        async def handler(query: str):
            return {"query": query}

        register_tool(
            name="no_ctx_test",
            description="test",
            category="test",
            parameters_schema={"type": "object"},
            handler=handler,
        )

        ctx = ToolContext(session_id="s2")
        result = asyncio.run(
            _integration.call_tool("no_ctx_test", tool_context=ctx, query="hi")
        )
        assert result == {"query": "hi"}

    def test_none_context_not_injected(self, _integration):
        received = {}

        async def handler(query: str, tool_context: ToolContext = None):
            received["ctx"] = tool_context
            return {"ok": True}

        register_tool(
            name="ctx_none_test",
            description="test",
            category="test",
            parameters_schema={"type": "object"},
            handler=handler,
        )

        asyncio.run(_integration.call_tool("ctx_none_test", query="hi"))
        assert received["ctx"] is None


# ---------------------------------------------------------------------------
# Full chain: execute_tool → call_tool → handler
# ---------------------------------------------------------------------------

class TestExecuteToolContextChain:
    def test_execute_tool_passes_context_through(self):
        received = {}

        async def handler(query: str, tool_context: ToolContext = None):
            received["ctx"] = tool_context
            return {"ok": True}

        register_tool(
            name="chain_test",
            description="test",
            category="test",
            parameters_schema={"type": "object"},
            handler=handler,
        )

        ctx = ToolContext(session_id="chain-sess", task_id=99)
        from tool_box import execute_tool
        asyncio.run(execute_tool("chain_test", query="go", tool_context=ctx))

        assert received["ctx"] is not None
        assert received["ctx"].session_id == "chain-sess"
        assert received["ctx"].task_id == 99


# ---------------------------------------------------------------------------
# UnifiedToolExecutor builds ToolContext from ToolExecutionContext
# ---------------------------------------------------------------------------

class TestUnifiedToolExecutorContext:
    @pytest.mark.asyncio
    async def test_executor_builds_tool_context_from_execution_context(self):
        from app.services.execution.tool_executor import (
            UnifiedToolExecutor,
            ToolExecutionContext,
        )

        captured_ctx = {}

        async def handler(query: str, tool_context: ToolContext = None):
            captured_ctx["ctx"] = tool_context
            return {"success": True, "summary": "ok"}

        register_tool(
            name="exec_ctx_test",
            description="test",
            category="test",
            parameters_schema={"type": "object"},
            handler=handler,
        )

        executor = UnifiedToolExecutor()
        exec_ctx = ToolExecutionContext(
            session_id="exec-sess",
            plan_id=10,
            task_id=5,
            task_name="My Task",
            current_job_id="job-42",
            work_dir="/tmp/demo-work",
            capability_floor="research",
        )

        await executor.execute("exec_ctx_test", {"query": "test"}, context=exec_ctx)

        ctx = captured_ctx.get("ctx")
        assert ctx is not None, "Handler should receive ToolContext"
        assert ctx.session_id == "exec-sess"
        assert ctx.plan_id == 10
        assert ctx.task_id == 5
        assert ctx.task_name == "My Task"
        assert ctx.job_id == "job-42"
        assert ctx.work_dir == "/tmp/demo-work"
        assert ctx.capability_floor == "research"
