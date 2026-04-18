from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.routers import agent_routes, decomposition_routes, tool_routes


@pytest.mark.asyncio
async def test_legacy_tool_routes_fail_closed():
    with pytest.raises(HTTPException) as exc_info:
        await tool_routes.get_task_tool_requirements(42)

    assert exc_info.value.status_code == 410
    assert "Legacy task-table tool endpoints" in exc_info.value.detail

    with pytest.raises(HTTPException) as exec_exc_info:
        await tool_routes.execute_task_with_tools_api(42, payload={})

    assert exec_exc_info.value.status_code == 410
    assert "plan_routes" in exec_exc_info.value.detail


def test_legacy_decomposition_routes_fail_closed():
    with pytest.raises(HTTPException) as exc_info:
        decomposition_routes.decompose_task_endpoint(7, payload={})

    assert exc_info.value.status_code == 410
    assert "PlanTree-backed planning stack" in exc_info.value.detail


@pytest.mark.asyncio
async def test_legacy_agent_routes_fail_closed():
    request = agent_routes.AgentRequest(goal="Build an analysis workflow")

    with pytest.raises(HTTPException) as exc_info:
        await agent_routes.create_agent_workflow(request)

    assert exc_info.value.status_code == 410
    assert "PlanTree-backed planning flow" in exc_info.value.detail
