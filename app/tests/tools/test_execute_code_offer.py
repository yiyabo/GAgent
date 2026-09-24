"""Offer gating tests: execute_code is invisible unless CODE_MODE_ENABLED=1.

Hard requirement: with the flag unset, the native-schema golden master must
stay byte-identical. With the flag on, the schema appears on every offer
surface with the dynamic signature list in its description.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import tool_schemas
from app.routers.chat.request_routing import get_all_tools
from tool_box.tools_impl.execute_code import execute_code_handler

PROJECT_ROOT = Path(__file__).resolve().parents[3]
GOLDEN_PATH = (
    PROJECT_ROOT / "app" / "tests" / "tools" / "fixtures" / "native_schemas_golden.json"
)

ALL_NATIVE_TOOLS = [
    "bio_tools",
    "code_executor",
    "deliverable_submit",
    "document_reader",
    "file_operations",
    "graph_rag",
    "literature_pipeline",
    "manuscript_writer",
    "phagescope",
    "phagescope_research",
    "plan_operation",
    "result_interpreter",
    "review_pack_writer",
    "scientific_figure_generator",
    "sequence_fetch",
    "terminal_session",
    "url_fetch",
    "verify_task",
    "vision_reader",
    "web_search",
]


@pytest.fixture()
def _code_mode_off(monkeypatch):
    monkeypatch.delenv("CODE_MODE_ENABLED", raising=False)
    tool_schemas._TOOL_REGISTRY_CACHE = None
    yield
    tool_schemas._TOOL_REGISTRY_CACHE = None


@pytest.fixture()
def _code_mode_on(monkeypatch):
    monkeypatch.setenv("CODE_MODE_ENABLED", "1")
    tool_schemas._TOOL_REGISTRY_CACHE = None
    yield
    tool_schemas._TOOL_REGISTRY_CACHE = None


def _normalized(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _names(schemas) -> list:
    return [schema["function"]["name"] for schema in schemas]


# --- OFF: zero surface change ---------------------------------------------------


def test_off_golden_master_byte_identical(_code_mode_off):
    payload = {
        "build_tool_schemas_all20": tool_schemas.build_tool_schemas(ALL_NATIVE_TOOLS),
        "build_executor_tool_schemas": tool_schemas.build_executor_tool_schemas(),
        "executor_available_tools": tool_schemas.EXECUTOR_AVAILABLE_TOOLS,
    }
    assert _normalized(payload) == GOLDEN_PATH.read_text(encoding="utf-8")


def test_off_execute_code_absent_from_every_offer_surface(_code_mode_off):
    registry = tool_schemas._get_tool_registry()
    assert "execute_code" not in registry
    assert "execute_code" not in _names(tool_schemas.build_tool_schemas(list(ALL_NATIVE_TOOLS)))
    assert "execute_code" not in _names(tool_schemas.build_executor_tool_schemas())
    # Even an explicit caller request cannot leak the schema when disabled.
    assert "execute_code" not in _names(tool_schemas.build_tool_schemas(["execute_code"]))
    assert "execute_code" not in get_all_tools()
    assert "execute_code" not in tool_schemas.EXECUTOR_AVAILABLE_TOOLS


@pytest.mark.asyncio()
async def test_off_handler_refuses_execution(_code_mode_off):
    result = await execute_code_handler(code="print(1)")
    assert result["success"] is False
    assert result["error"] == "code_mode_disabled"


# --- ON: schema appears with dynamic signature list ------------------------------


def test_on_execute_code_present_on_every_offer_surface(_code_mode_on):
    registry = tool_schemas._get_tool_registry()
    assert "execute_code" in registry
    schema = registry["execute_code"]
    assert schema["type"] == "function"
    params = schema["function"]["parameters"]
    assert params["required"] == ["code"]
    assert set(params["properties"]) == {"code", "reset"}

    native = _names(tool_schemas.build_tool_schemas(list(ALL_NATIVE_TOOLS)))
    assert "execute_code" in native
    executor = _names(tool_schemas.build_executor_tool_schemas())
    assert "execute_code" in executor
    assert "execute_code" in get_all_tools()


def test_on_description_carries_dynamic_signature_list(_code_mode_on):
    registry = tool_schemas._get_tool_registry()
    description = registry["execute_code"]["function"]["description"]
    assert "PERSISTENT kernel" in description
    assert "web_search(query: str" in description
    assert "sequence_fetch(" in description
    assert "50 tool calls" in description


def test_default_allowlist_excludes_legacy_graph_rag(_code_mode_on):
    """Decision lock (2026-09-24): graph_rag is LEGACY in its own schema and
    lightrag_query covers the same ground; it stays opt-in via
    CODE_MODE_ALLOWED_TOOLS."""
    from tool_box.tools_impl.execute_code.config import DEFAULT_ALLOWED_TOOLS, allowed_tools

    assert "graph_rag" not in DEFAULT_ALLOWED_TOOLS
    assert allowed_tools() == list(DEFAULT_ALLOWED_TOOLS)
    description = tool_schemas._get_tool_registry()["execute_code"]["function"]["description"]
    assert "lightrag_query(" in description
    assert "graph_rag(" not in description


def test_allowlist_override_can_add_graph_rag_back(_code_mode_on, monkeypatch):
    monkeypatch.setenv("CODE_MODE_ALLOWED_TOOLS", "graph_rag,web_search")
    tool_schemas._TOOL_REGISTRY_CACHE = None
    description = tool_schemas._get_tool_registry()["execute_code"]["function"]["description"]
    assert "graph_rag(" in description


def test_on_allowlist_override_rewrites_signature_list(_code_mode_on, monkeypatch):
    monkeypatch.setenv("CODE_MODE_ALLOWED_TOOLS", "web_search,url_fetch")
    tool_schemas._TOOL_REGISTRY_CACHE = None
    registry = tool_schemas._get_tool_registry()
    description = registry["execute_code"]["function"]["description"]
    assert "web_search(query: str" in description
    assert "url_fetch(url: str" in description
    assert "sequence_fetch(" not in description


def test_on_golden_fixture_intentionally_diverges(_code_mode_on):
    """Lock the intent: the golden fixture captures the OFF state only."""
    payload = {
        "build_tool_schemas_all20": tool_schemas.build_tool_schemas(ALL_NATIVE_TOOLS),
        "build_executor_tool_schemas": tool_schemas.build_executor_tool_schemas(),
        "executor_available_tools": tool_schemas.EXECUTOR_AVAILABLE_TOOLS,
    }
    assert _normalized(payload) != GOLDEN_PATH.read_text(encoding="utf-8")
