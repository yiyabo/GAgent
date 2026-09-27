"""Progressive tool-schema disclosure (2026-09-27).

Iteration 1 sends the full ~40KB tools payload; iteration 2+ trims to
used-so-far ∪ CORE_KEEP ∪ loaded ∪ {load_tool_schema}; a tool call the
trimmed payload cannot serve flips the run back to full (escape hatch).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app.services.deep_think import dispatch as dispatch_module
from app.services.deep_think.schema_disclosure import (
    CORE_KEEP_TOOLS,
    META_TOOL_NAME,
    SUBMIT_FINAL_ANSWER_NAME,
    SchemaDisclosure,
)


def _schema(name: str) -> Dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": f"{name} desc", "parameters": {"type": "object", "properties": {}}}}


_FULL_NAMES = [
    "web_search",
    "file_operations",
    "execute_code",
    "deliverable_submit",
    "vision_reader",
    "document_reader",
    "literature_pipeline",
    "phagescope",
    "plan_operation",
    SUBMIT_FINAL_ANSWER_NAME,
]
_FULL = [_schema(name) for name in _FULL_NAMES]
_AVAILABLE = [name for name in _FULL_NAMES if name != SUBMIT_FINAL_ANSWER_NAME]


def _names(schemas: List[Dict[str, Any]]) -> List[str]:
    return [schema["function"]["name"] for schema in schemas]


@pytest.fixture()
def disclosure() -> SchemaDisclosure:
    return SchemaDisclosure(_FULL, _AVAILABLE)


def test_iteration_one_sends_full_payload(disclosure: SchemaDisclosure) -> None:
    assert _names(disclosure.effective(iteration=1, tools_used=[])) == _FULL_NAMES


def test_later_iterations_trim_to_used_core_and_meta(disclosure: SchemaDisclosure) -> None:
    trimmed = _names(disclosure.effective(iteration=2, tools_used=["literature_pipeline"]))

    assert "literature_pipeline" in trimmed  # used-so-far
    for core in CORE_KEEP_TOOLS:
        assert core in trimmed
    assert SUBMIT_FINAL_ANSWER_NAME in trimmed
    assert META_TOOL_NAME in trimmed
    # Everything else is gone — that is the token saving.
    assert "vision_reader" not in trimmed
    assert "document_reader" not in trimmed
    assert "phagescope" not in trimmed
    assert "plan_operation" not in trimmed  # not plan-bound here


def test_plan_bound_run_keeps_plan_operation(disclosure: SchemaDisclosure) -> None:
    trimmed = _names(disclosure.effective(iteration=3, tools_used=[], plan_bound=True))
    assert "plan_operation" in trimmed


def test_record_load_adds_schema_next_round(disclosure: SchemaDisclosure) -> None:
    result = disclosure.record_load("vision_reader")
    assert result["success"] is True
    assert result["loaded"] is True

    trimmed = _names(disclosure.effective(iteration=2, tools_used=[]))
    assert "vision_reader" in trimmed

    second = disclosure.record_load("vision_reader")
    assert second["success"] is True
    assert second["loaded"] is False


@pytest.mark.parametrize("bad", ["", META_TOOL_NAME, SUBMIT_FINAL_ANSWER_NAME, "not_a_tool"])
def test_record_load_rejects_unknown_or_meta(disclosure: SchemaDisclosure, bad: str) -> None:
    result = disclosure.record_load(bad)
    assert result["success"] is False
    assert result["error"] == "unknown_tool"
    assert _names(disclosure.effective(iteration=2, tools_used=[])) == _names(
        disclosure.effective(iteration=2, tools_used=[])
    )


def test_force_full_escape_restores_full_payload(disclosure: SchemaDisclosure) -> None:
    assert len(disclosure.effective(iteration=2, tools_used=[])) < len(_FULL)
    disclosure.force_full("tool_not_available:phagescope")
    assert disclosure.force_full_active is True
    assert _names(disclosure.effective(iteration=3, tools_used=[])) == _FULL_NAMES


def test_env_kill_switch_restores_always_full(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCHEMA_PROGRESSIVE_ENABLED", "0")
    off = SchemaDisclosure(_FULL, _AVAILABLE)
    assert off.enabled is False
    assert _names(off.effective(iteration=9, tools_used=[])) == _FULL_NAMES


def test_meta_schema_is_a_valid_function_envelope(disclosure: SchemaDisclosure) -> None:
    meta = disclosure.meta_schema()
    assert meta["type"] == "function"
    assert meta["function"]["name"] == META_TOOL_NAME
    assert meta["function"]["parameters"]["required"] == ["name"]


# --- dispatch wiring -----------------------------------------------------------


def _agent_stub(available: List[str]) -> Any:
    from app.services.deep_think_agent import DeepThinkAgent

    async def _noop_executor(name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        return {"success": True}

    return DeepThinkAgent(
        llm_client=SimpleNamespace(),
        available_tools=available,
        tool_executor=_noop_executor,
        request_profile={"session_id": "session_disclosure_test"},
    )


def test_dispatch_intercepts_load_tool_schema_without_registry() -> None:
    agent = _agent_stub(_AVAILABLE)
    agent._schema_disclosure = SchemaDisclosure(_FULL, _AVAILABLE)
    tc = SimpleNamespace(name=META_TOOL_NAME, arguments={"name": "vision_reader"}, id="call_1")

    entry = asyncio.run(dispatch_module._execute_native_tool_call(agent, tc, 2, 0))

    assert entry["tool_result"]["success"] is True
    assert entry["tool_result"]["tool"] == "vision_reader"
    assert "vision_reader" in agent._schema_disclosure.loaded
    # The meta tool is not in available_tools — interception happens before
    # the availability refusal, so this must not be a tool_not_available entry.
    assert "tool_not_available" not in str(entry["tool_result"].get("error") or "")


def test_dispatch_tool_not_available_flips_force_full() -> None:
    agent = _agent_stub(["web_search"])
    agent._schema_disclosure = SchemaDisclosure(_FULL, _AVAILABLE)
    tc = SimpleNamespace(name="vision_reader", arguments={}, id="call_2")

    entry = asyncio.run(dispatch_module._execute_native_tool_call(agent, tc, 2, 0))

    assert entry["tool_result"]["success"] is False
    assert "tool_not_available" in entry["tool_result"]["error"]
    assert agent._schema_disclosure.force_full_active is True
