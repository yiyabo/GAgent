"""``code_executor`` offer-side exclusion list, symmetric with ``delegate_task``.

2026-09-26: ``delegate_task`` shipped with an explicit exclusion list ("Do NOT
use ... when one tool call or two already answers the question"), while
``code_executor`` — the pi-harness delegation — had only a read-only clause.
Evaluation evidence showed the asymmetry costs real time: ``t01_read_csv_rows``
(counting rows in a CSV the model already had) paid a full pi delegation, whose
measured floor is 35-57s wall clock plus 3.2-3.5k sub-agent tokens.

The list is a *description* contract, so it is locked per offer surface: the
native function-calling schema, the legacy prompt catalog, and the impl/registry
definition the plan executor reads.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services.deep_think_agent import DeepThinkAgent
from tool_box.tools_impl.code_executor import code_executor_tool


async def _noop_tool_executor(_name: str, _params: dict[str, Any]) -> dict[str, bool]:
    return {"success": True}


def _agent() -> DeepThinkAgent:
    return DeepThinkAgent(
        llm_client=SimpleNamespace(),
        available_tools=["code_executor", "execute_code", "file_operations"],
        tool_executor=_noop_tool_executor,
        request_profile=None,
    )


def _native_description() -> str:
    from tool_box.native_tool_schemas import NATIVE_TOOL_CONTENT

    return NATIVE_TOOL_CONTENT["code_executor"]["description"]


def _prompt_catalog_description() -> str:
    """The legacy prompt catalog entry for code_executor, out of the real prompt."""
    prompt = _agent()._build_system_prompt()
    line = next(
        (ln for ln in prompt.splitlines() if ln.startswith("- code_executor:")),
        "",
    )
    assert line, "code_executor missing from the legacy prompt tool catalog"
    return line


# Every clause the offer-side guidance must keep. Each entry is the cheapest
# answer among the tools the model already has, so re-delegating it is pure cost.
_EXCLUSION_CLAUSES = (
    "single file",
    "counting",
    "arithmetic",
    "single plot",
    "read-only",
)


def _assert_exclusion_clauses(description: str) -> None:
    for clause in _EXCLUSION_CLAUSES:
        assert clause in description, f"missing exclusion clause: {clause}"


def test_native_schema_carries_the_exclusion_list() -> None:
    _assert_exclusion_clauses(_native_description())


def test_native_schema_states_the_delegation_start_up_cost() -> None:
    """The list is only actionable with its price: name the up-front agent run."""
    description = _native_description()
    assert "30-60s" in description
    assert "DELEGATION" in description


def test_impl_and_registry_schema_carries_the_exclusion_list() -> None:
    _assert_exclusion_clauses(code_executor_tool["description"])


def test_prompt_catalog_carries_the_exclusion_list() -> None:
    description = _prompt_catalog_description()
    for clause in ("单文件读取", "一次性统计", "算术", "单张图", "只读检查"):
        assert clause in description, f"missing exclusion clause: {clause}"
    assert "30-60s" in description


def test_the_two_delegation_surfaces_agree_on_the_start_up_cost() -> None:
    """Both delegations pay a whole agent run; neither may read as the cheap path."""
    from tool_box.tools_impl.delegate_task import DESCRIPTION as delegate_description

    assert "Do NOT use delegate_task when" in delegate_description
    assert "Do NOT delegate" in _native_description()
    assert "Do NOT delegate" in code_executor_tool["description"]


@pytest.mark.parametrize(
    ("surface", "keep_clause"),
    [
        (_native_description, "substantive implementation work"),
        (_prompt_catalog_description, "复杂实现任务"),
    ],
    ids=["native", "prompt"],
)
def test_exclusion_list_does_not_shadow_the_real_use_case(surface, keep_clause) -> None:
    """The exclusion must not talk the model out of the tool's actual job."""
    assert keep_clause in surface()


# ---------------------------------------------------------------------------
# Drift locks on the routing copy (2026-09-26)
#
# A/B evidence (28-task arms): the same task lands at 25-80s when the model
# writes the script itself (execute_code) and at 100-900s when it delegates,
# yet the delegation was framed as the "PRIMARY TOOL" and as "pi coding
# harness"/"Claude Code" — a harness that `auto+split` does not even use for
# analysis work. The copy pushed the model onto the expensive lane and
# described a lane it may never get.
# ---------------------------------------------------------------------------

_HARNESS_NAMES = ("pi coding harness", "claude code", "qwen code", "qwen_code")


def test_delegation_copy_names_no_specific_harness() -> None:
    for surface in (_native_description(), code_executor_tool["description"]):
        lowered = surface.lower()
        for harness in _HARNESS_NAMES:
            assert harness not in lowered, f"delegation copy still names {harness!r}"
        assert "deployment-configured" in lowered


def test_delegation_copy_does_not_sell_itself_as_the_default() -> None:
    """It is the expensive lane; the copy may not read as the primary tool."""
    for surface in (_native_description(), code_executor_tool["description"]):
        assert "PRIMARY TOOL" not in surface


def test_code_mode_copy_claims_the_one_script_case() -> None:
    """The cheap path has to say out loud what it is cheap for."""
    from tool_box.native_tool_schemas import NATIVE_TOOL_CONTENT
    from tool_box.tools_impl.execute_code.tool import BASE_DESCRIPTION

    for surface in (BASE_DESCRIPTION, NATIVE_TOOL_CONTENT["execute_code"]["description"]):
        lowered = surface.lower()
        assert "cheap path" in lowered
        assert "single plot" in lowered
        assert "execute_code is YOU writing Python" in surface
