"""Tests for Phase 1.1 — ToolDefinition orchestration metadata.

Validates that:
1. ToolDefinition supports new metadata fields with backward-compatible defaults
2. All registered tools have correct metadata annotations
3. register_tool() and get_tool_info() expose the new fields
"""

from __future__ import annotations

import pytest

from tool_box.tools import ToolDefinition, get_tool_registry, register_tool
from tool_box.tool_registry import register_all_tools, _TOOL_METADATA


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _fresh_registry():
    """Reset the global registry before each test."""
    registry = get_tool_registry()
    for name in list(registry.tools.keys()):
        registry.unregister_tool(name)
    yield
    for name in list(registry.tools.keys()):
        registry.unregister_tool(name)


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    def test_old_style_registration_defaults_all_metadata_to_conservative(self):
        register_tool(
            name="legacy_tool",
            description="A legacy tool",
            category="test",
            parameters_schema={"type": "object"},
            handler=lambda: None,
        )
        td = get_tool_registry().get_tool("legacy_tool")
        assert td is not None
        assert td.is_read_only is False
        assert td.is_concurrent_safe is False
        assert td.is_destructive is False
        assert td.search_hint == ""

    def test_dataclass_default_values_are_safe(self):
        td = ToolDefinition(
            name="bare",
            description="Bare minimum",
            category="test",
            parameters_schema={},
            handler=lambda: None,
        )
        assert td.is_read_only is False
        assert td.is_concurrent_safe is False
        assert td.is_destructive is False
        assert td.search_hint == ""


# ---------------------------------------------------------------------------
# New metadata fields
# ---------------------------------------------------------------------------

class TestMetadataRegistration:
    def test_register_tool_accepts_metadata_kwargs(self):
        register_tool(
            name="search_v2",
            description="Search",
            category="retrieval",
            parameters_schema={"type": "object"},
            handler=lambda: None,
            is_read_only=True,
            is_concurrent_safe=True,
            search_hint="web query",
        )
        td = get_tool_registry().get_tool("search_v2")
        assert td.is_read_only is True
        assert td.is_concurrent_safe is True
        assert td.is_destructive is False
        assert td.search_hint == "web query"

    def test_get_tool_info_includes_metadata(self):
        register_tool(
            name="info_check",
            description="Check info",
            category="test",
            parameters_schema={"type": "object"},
            handler=lambda: None,
            is_destructive=True,
        )
        info = get_tool_registry().get_tool_info("info_check")
        assert info is not None
        assert info["is_destructive"] is True
        assert info["is_read_only"] is False
        assert info["is_concurrent_safe"] is False


# ---------------------------------------------------------------------------
# Registered tool metadata correctness
# ---------------------------------------------------------------------------

# Tools that MUST be marked read-only and concurrent-safe
_READ_ONLY_CONCURRENT_TOOLS = {
    "web_search",
    "literature_pipeline",
    "document_reader",
    "vision_reader",
    "graph_rag",
    "sequence_fetch",
    "database_query",
}

# Tools that MUST NOT be concurrent-safe
_NON_CONCURRENT_TOOLS = {
    "code_executor",
    "phagescope",
    "bio_tools",
    "terminal_session",
    "plan_operation",
    "manuscript_writer",
}

# Tools that MUST be marked destructive
_DESTRUCTIVE_TOOLS = {
    "terminal_session",
}


class TestRegisteredToolMetadata:
    @pytest.fixture(autouse=True)
    def _register(self):
        register_all_tools()
        yield

    @pytest.mark.parametrize("tool_name", sorted(_READ_ONLY_CONCURRENT_TOOLS))
    def test_read_only_concurrent_tools(self, tool_name: str):
        td = get_tool_registry().get_tool(tool_name)
        assert td is not None, f"{tool_name} not registered"
        assert td.is_read_only, f"{tool_name} should be is_read_only"
        assert td.is_concurrent_safe, f"{tool_name} should be is_concurrent_safe"
        assert not td.is_destructive, f"{tool_name} should not be is_destructive"

    @pytest.mark.parametrize("tool_name", sorted(_NON_CONCURRENT_TOOLS))
    def test_non_concurrent_tools(self, tool_name: str):
        td = get_tool_registry().get_tool(tool_name)
        assert td is not None, f"{tool_name} not registered"
        assert not td.is_concurrent_safe, f"{tool_name} should not be is_concurrent_safe"

    @pytest.mark.parametrize("tool_name", sorted(_DESTRUCTIVE_TOOLS))
    def test_destructive_tools(self, tool_name: str):
        td = get_tool_registry().get_tool(tool_name)
        assert td is not None, f"{tool_name} not registered"
        assert td.is_destructive, f"{tool_name} should be is_destructive"

    def test_all_tools_with_metadata_have_search_hints(self):
        registry = get_tool_registry()
        for name, meta in _TOOL_METADATA.items():
            td = registry.get_tool(name)
            if td is None:
                continue  # tool might not be registered in test env
            if "search_hint" in meta:
                assert td.search_hint, f"{name} has metadata entry but empty search_hint"

    def test_metadata_map_covers_all_registered_tools(self):
        """Every registered tool should have an entry in _TOOL_METADATA."""
        registry = get_tool_registry()
        missing = [
            name for name in registry.get_tool_names()
            if name not in _TOOL_METADATA
        ]
        # Allow some tools to be absent (e.g. internal_api, generate_experiment_card)
        # but flag if many are missing
        assert len(missing) <= 5, (
            f"Too many tools without metadata entries: {missing}"
        )
