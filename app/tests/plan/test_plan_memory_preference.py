"""A durable Plan task must inherit the initiating turn's memory choice."""

import pytest

from app.services.memory import memory_service
from app.services.plans.execution_budget import request_profile
from app.services.plans.plan_executor import (
    ExecutionConfig,
    PlanExecutor,
    ToolCallRequest,
)
from app.services.plans.plan_models import PlanNode


@pytest.mark.parametrize("operation", ["list", "save"])
def test_plan_tool_dispatch_honors_memory_opt_out(monkeypatch, tmp_path, operation):
    def unexpected_access():
        pytest.fail("An opted-out Plan task opened the memory service")

    monkeypatch.setattr(memory_service, "get_memory_service", unexpected_access)
    monkeypatch.setattr(PlanExecutor, "_resolve_task_tool_workspace", lambda *args, **kwargs: ([], str(tmp_path)))
    node = PlanNode(id=2, plan_id=7, name="Summarize notes", status="pending")
    executor = PlanExecutor()
    context = {"owner_id": "alice", "session_id": "session_test", "memory_enabled": False}
    assert request_profile(node.plan_id, node.id, context)["memory_enabled"] is False
    payload = executor._execute_tool_call(
        ToolCallRequest(name="memory_operation", parameters={"operation": operation, "content": "example note"}),
        node,
        ExecutionConfig(session_context=context),
    )
    assert payload["success"] is False
    assert payload["result"]["memory_disabled"] is True
