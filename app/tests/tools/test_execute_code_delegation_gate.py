"""Delegation pointer gate on the execute_code description (LOCAL_INFRA §115).

With the pi delegation lane closed in production (``DELEGATE_TASK_ENABLED=0``),
the execute_code copy must not tell the model to hand long goals to a tool it
cannot call. The static surfaces keep the pointer as the enabled-state truth
(native mirror + drift lock untouched); every *built* description swaps it out
while the gate is off.
"""

from __future__ import annotations

import pytest

from app.services import tool_schemas
from app.services.deep_think import prompts
from tool_box.native_tool_schemas import NATIVE_TOOL_CONTENT
from tool_box.tools_impl.execute_code import config as code_mode_config
from tool_box.tools_impl.execute_code.tool import (
    BASE_DESCRIPTION,
    DELEGATE_POINTER,
    DELEGATE_POINTER_OFF,
    apply_delegation_gate,
    build_description,
)

_NATIVE = NATIVE_TOOL_CONTENT["execute_code"]


def test_pointer_text_is_still_present_on_both_static_surfaces() -> None:
    """The gate is a text swap: a reworded pointer must fail here, not silently no-op."""
    assert DELEGATE_POINTER in BASE_DESCRIPTION
    assert DELEGATE_POINTER in _NATIVE["description"]
    assert "delegate_task" not in DELEGATE_POINTER_OFF


def test_apply_delegation_gate_swaps_pointer_only_when_off() -> None:
    assert apply_delegation_gate(BASE_DESCRIPTION, enabled=True) == BASE_DESCRIPTION
    gated = apply_delegation_gate(BASE_DESCRIPTION, enabled=False)
    assert "delegate_task" not in gated
    assert DELEGATE_POINTER_OFF in gated
    assert gated.startswith("Run Python that calls GAgent tools")
    assert gated.endswith("Available functions (from gagent_tools import ...):")


@pytest.mark.parametrize(
    "flag, expect_pointer",
    [("1", True), ("0", False), (None, False)],
)
def test_env_flag_drives_every_built_surface(monkeypatch, flag, expect_pointer) -> None:
    if flag is None:
        monkeypatch.delenv(code_mode_config.ENV_DELEGATE_ENABLED, raising=False)
    else:
        monkeypatch.setenv(code_mode_config.ENV_DELEGATE_ENABLED, flag)
    assert code_mode_config.delegate_task_offered() is expect_pointer

    surfaces = {
        "impl": build_description(allowed=["web_search"], progressive=False),
        "native": tool_schemas._build_execute_code_description(_NATIVE),
        "legacy_prompt": prompts._execute_code_catalog_entry(),
    }
    for name, text in surfaces.items():
        assert ("delegate_task" in text) is expect_pointer, name
        assert "default way to run code" in text, name
        if not expect_pointer:
            assert "drive it here in stages" in text, name
