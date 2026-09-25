"""Offer gating: ``delegate_task`` is invisible unless DELEGATE_TASK_ENABLED=1.

Mirrors the code-mode gating contract: with the flag unset the native-schema
golden master stays byte-identical and the tool exists on no offer surface; with
the flag on it appears on the chat surfaces with a valid envelope and is
dispatchable through the registry.
"""

from __future__ import annotations

from typing import Any, List

import pytest

from app.routers.chat.request_routing import get_all_tools
from app.services import tool_schemas
from app.tests.tools.test_native_tool_schemas import (
    ALL_NATIVE_TOOLS,
    GOLDEN_PATH,
    _normalized,
    without_gated_tools,
)
from tool_box.tool_registry import get_tool_orchestration_metadata, register_all_tools
from tool_box.tools import get_tool_registry
from tool_box.tools_impl.delegate_task import delegate_task_tool

_TOOL = "delegate_task"


@pytest.fixture(autouse=True)
def _unregister_after_each_test():
    """The runtime tool registry is global: a delegation registered under the
    flag-on fixtures must not leak into later tests."""
    yield
    registry = get_tool_registry()
    if registry.get_tool(_TOOL) is not None:
        registry.unregister_tool(_TOOL)


@pytest.fixture()
def _flag_off(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DELEGATE_TASK_ENABLED", raising=False)
    tool_schemas._TOOL_REGISTRY_CACHE = None
    yield
    tool_schemas._TOOL_REGISTRY_CACHE = None


@pytest.fixture()
def _flag_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DELEGATE_TASK_ENABLED", "1")
    tool_schemas._TOOL_REGISTRY_CACHE = None
    yield
    tool_schemas._TOOL_REGISTRY_CACHE = None


def _names(schemas: List[dict]) -> List[str]:
    return [schema["function"]["name"] for schema in schemas]


# --- OFF: zero surface change ------------------------------------------------------


def test_off_golden_master_byte_identical(_flag_off) -> None:
    payload = {
        "build_tool_schemas_all20": without_gated_tools(
            tool_schemas.build_tool_schemas(ALL_NATIVE_TOOLS)
        ),
        "build_executor_tool_schemas": without_gated_tools(
            tool_schemas.build_executor_tool_schemas()
        ),
        "executor_available_tools": tool_schemas.EXECUTOR_AVAILABLE_TOOLS,
    }
    assert _normalized(payload) == GOLDEN_PATH.read_text(encoding="utf-8")


def test_off_absent_from_every_offer_surface(_flag_off) -> None:
    registry = tool_schemas._get_tool_registry()
    assert _TOOL not in registry
    assert _TOOL not in _names(tool_schemas.build_tool_schemas(list(ALL_NATIVE_TOOLS)))
    # Even an explicit caller request cannot leak the schema when disabled.
    assert _TOOL not in _names(tool_schemas.build_tool_schemas([_TOOL]))
    assert _TOOL not in _names(tool_schemas.build_executor_tool_schemas())
    assert _TOOL not in get_all_tools()
    assert _TOOL not in tool_schemas.EXECUTOR_AVAILABLE_TOOLS


def test_off_not_registered_in_the_runtime_registry(_flag_off) -> None:
    register_all_tools()

    assert get_tool_registry().get_tool(_TOOL) is None


# --- ON: present on the chat surfaces with a valid envelope -------------------------


def test_on_present_on_the_chat_offer_surfaces(_flag_on) -> None:
    registry = tool_schemas._get_tool_registry()
    assert _TOOL in registry
    schema = registry[_TOOL]
    assert schema["type"] == "function"
    assert schema["function"]["name"] == _TOOL
    params = schema["function"]["parameters"]
    assert params["required"] == ["goal"]
    assert set(params["properties"]) == {"goal", "deliverable", "context_paths"}
    assert params["properties"]["context_paths"]["items"] == {"type": "string"}

    assert _TOOL in _names(tool_schemas.build_tool_schemas(list(ALL_NATIVE_TOOLS)))
    assert _TOOL in get_all_tools()
    # The submitted native envelope still terminates the DeepThink tool list.
    assert (
        tool_schemas.build_tool_schemas([_TOOL])[-1]
        is tool_schemas.SUBMIT_FINAL_ANSWER_SCHEMA
    )


def test_on_plan_executor_pool_stays_static(_flag_on) -> None:
    """Decision lock: delegate_task is the chat-side delegation surface. Plan
    tasks already delegate through PlanExecutor's own path, so handing the plan
    pool a second delegation tool would let a sub-agent spawn sub-agents."""
    assert _TOOL not in tool_schemas.EXECUTOR_AVAILABLE_TOOLS
    assert _TOOL not in _names(tool_schemas.build_executor_tool_schemas())


def test_on_registered_with_orchestration_metadata(_flag_on) -> None:
    register_all_tools()
    tool_def = get_tool_registry().get_tool(_TOOL)

    assert tool_def is not None
    assert tool_def.category == "execution"
    assert tool_def.handler is delegate_task_tool["handler"]
    assert tool_def.is_read_only is False
    assert tool_def.is_destructive is False
    # Serial by design: several delegate_task calls must not run concurrently.
    assert tool_def.is_concurrent_safe is False
    metadata = get_tool_orchestration_metadata(_TOOL)
    assert "subagent" in metadata["search_hint"]


def test_description_carries_the_three_lane_division_of_labor(_flag_on) -> None:
    description = tool_schemas._get_tool_registry()[_TOOL]["function"]["description"]

    assert "code_executor" in description
    assert "execute_code" in description
    assert "Do NOT use delegate_task when" in description
    assert "Good: goal=" in description
    assert "Bad: goal=" in description
    assert "transcript never enters your context" in description
    assert "no parallel fan-out" in description


async def test_on_dispatch_through_the_registry_reaches_the_handler(
    _flag_on, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Canonical dispatch path: registry lookup + prepare_handler_kwargs."""
    from app.services.plans.task_delegate_executor import TaskDelegationResult
    from tool_box import execute_tool
    from tool_box.context import ToolContext

    captured: dict[str, Any] = {}

    class _StubExecutor:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def execute(self, spec: Any) -> TaskDelegationResult:
            captured["spec"] = spec
            return TaskDelegationResult(
                status="completed",
                summary="Delegation ran through the registry.",
                artifact_paths=["/data/out/report.md"],
                raw_result={"run_id": "run-registry-1"},
            )

    monkeypatch.setattr(
        "app.services.plans.task_delegate_executor.CodeAgentTaskDelegateExecutor",
        _StubExecutor,
    )
    register_all_tools()

    result = await execute_tool(
        _TOOL,
        goal="Sweep the corpus",
        tool_context=ToolContext(session_id="session-registry", owner_id="owner-1"),
    )

    assert result["tool"] == _TOOL
    assert result["success"] is True
    assert result["summary"] == "Delegation ran through the registry."
    assert result["artifact_paths"] == ["/data/out/report.md"]
    assert result["trace_ref"]["run_id"] == "run-registry-1"
    assert captured["spec"].session_id == "session-registry"
    assert captured["spec"].plan_id is None
    assert captured["spec"].task_id is None
