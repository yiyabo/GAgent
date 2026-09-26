"""Offer gating tests: code_executor is invisible unless CODE_EXECUTOR_ENABLED=1.

Decision 2026-09-27: the delegated-coding harness steps aside by default —
``execute_code`` (code mode) owns one-script coding and ``delegate_task`` owns
whole-goal delegation. The gate is offer-side only: the handler stays callable
(plan/evals/scripts import it directly), so only the schema/pool surfaces are
asserted here. With the flag unset, the native-schema golden master must stay
byte-identical.
"""

from __future__ import annotations

import pytest

from app.services import tool_schemas
from app.routers.chat.request_routing import get_all_tools
from app.tests.tools.test_native_tool_schemas import (
    ALL_NATIVE_TOOLS,
    GOLDEN_PATH,
    _normalized,
    without_gated_tools,
)


@pytest.fixture()
def _code_executor_off(monkeypatch):
    monkeypatch.delenv("CODE_EXECUTOR_ENABLED", raising=False)
    tool_schemas._TOOL_REGISTRY_CACHE = None
    yield
    tool_schemas._TOOL_REGISTRY_CACHE = None


@pytest.fixture()
def _code_executor_on(monkeypatch):
    monkeypatch.setenv("CODE_EXECUTOR_ENABLED", "1")
    tool_schemas._TOOL_REGISTRY_CACHE = None
    yield
    tool_schemas._TOOL_REGISTRY_CACHE = None


def _names(schemas) -> list:
    return [schema["function"]["name"] for schema in schemas]


# --- OFF (production default): zero surface change ------------------------------


def test_off_golden_master_byte_identical(_code_executor_off):
    # The fixture records the all-gates-off payload; without_gated_tools() drops
    # whichever other env-gated entries happen to be enabled in this shell.
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


def test_off_code_executor_absent_from_every_offer_surface(_code_executor_off):
    registry = tool_schemas._get_tool_registry()
    assert "code_executor" not in registry
    assert "code_executor" not in _names(tool_schemas.build_tool_schemas(list(ALL_NATIVE_TOOLS)))
    assert "code_executor" not in _names(tool_schemas.build_executor_tool_schemas())
    # Even an explicit caller request cannot leak the schema when disabled.
    assert "code_executor" not in _names(tool_schemas.build_tool_schemas(["code_executor"]))
    assert "code_executor" not in get_all_tools()


def test_off_static_executor_pool_keeps_the_name_but_offers_no_schema(_code_executor_off):
    # EXECUTOR_AVAILABLE_TOOLS stays static (golden master + plan-executor import
    # contract); the registry skip is what removes the offered schema.
    assert "code_executor" in tool_schemas.EXECUTOR_AVAILABLE_TOOLS
    assert "code_executor" not in _names(tool_schemas.build_executor_tool_schemas())


# --- ON (opt-in / rollback): schema returns to every surface --------------------


def test_on_code_executor_present_on_every_offer_surface(_code_executor_on):
    registry = tool_schemas._get_tool_registry()
    assert "code_executor" in registry
    schema = registry["code_executor"]
    assert schema["type"] == "function"
    params = schema["function"]["parameters"]
    assert "task" in params["properties"]

    # Production supplies the name via get_all_tools() (which appends it when
    # the flag is on); the static ALL_NATIVE_TOOLS pool deliberately stays
    # gate-free, so the caller requests it explicitly here.
    native = _names(tool_schemas.build_tool_schemas(list(ALL_NATIVE_TOOLS) + ["code_executor"]))
    assert "code_executor" in native
    executor = _names(tool_schemas.build_executor_tool_schemas())
    assert "code_executor" in executor
    assert "code_executor" in get_all_tools()


def test_on_golden_fixture_intentionally_diverges(_code_executor_on):
    """Lock the intent: the golden fixture captures the OFF state only."""
    payload = {
        "build_tool_schemas_all20": tool_schemas.build_tool_schemas(ALL_NATIVE_TOOLS),
        "build_executor_tool_schemas": tool_schemas.build_executor_tool_schemas(),
        "executor_available_tools": tool_schemas.EXECUTOR_AVAILABLE_TOOLS,
    }
    assert _normalized(payload) != GOLDEN_PATH.read_text(encoding="utf-8")
