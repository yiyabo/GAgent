"""Native tool schema single-source tests.

- Golden master: the exact schema payload the LLM sees must stay byte-identical
  (normalized via json.dumps sort_keys) to the pre-refactor output captured in
  fixtures/native_schemas_golden.json.
- Generator smoke: a new tool only needs a content entry (description +
  parameters); the OpenAI function envelope is generated mechanically.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from app.services import tool_schemas
from app.services.tool_schemas import (
    EXECUTOR_AVAILABLE_TOOLS,
    TOOL_REGISTRY,
    build_executor_tool_schemas,
    build_tool_schemas,
)

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


def _normalized(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def test_native_schemas_match_golden_master() -> None:
    payload = {
        "build_tool_schemas_all20": build_tool_schemas(ALL_NATIVE_TOOLS),
        "build_executor_tool_schemas": build_executor_tool_schemas(),
        "executor_available_tools": EXECUTOR_AVAILABLE_TOOLS,
    }
    golden = GOLDEN_PATH.read_text(encoding="utf-8")
    assert _normalized(payload) == golden


def test_registry_has_all_native_tools_with_valid_envelopes() -> None:
    assert sorted(TOOL_REGISTRY.keys()) == sorted(ALL_NATIVE_TOOLS)
    for name, schema in TOOL_REGISTRY.items():
        assert schema["type"] == "function"
        fn = schema["function"]
        assert fn["name"] == name
        assert isinstance(fn["description"], str) and fn["description"]
        params = fn["parameters"]
        assert params["type"] == "object"
        assert isinstance(params["properties"], dict) and params["properties"]


def test_bio_tools_enum_stays_dynamic_from_tools_config() -> None:
    config_path = PROJECT_ROOT / "tool_box" / "bio_tools" / "tools_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    expected = sorted(set(config.keys()))
    schema = TOOL_REGISTRY["bio_tools"]
    enum_values = schema["function"]["parameters"]["properties"]["tool_name"]["enum"]
    assert enum_values == expected
    assert "tools_config.json" in schema["function"]["description"]


@pytest.fixture()
def _fresh_registry():
    """Reset the lazy registry cache around mutation-based tests."""
    tool_schemas._TOOL_REGISTRY_CACHE = None
    yield
    tool_schemas._TOOL_REGISTRY_CACHE = None


def test_new_tool_gets_generated_envelope_without_handwritten_shell(_fresh_registry) -> None:
    """A tools_impl-style content entry is enough: the OpenAI envelope and the
    registry/builder wiring are generated — no hand-written function shell."""
    from tool_box import native_tool_schemas

    fake_name = "smoke_fake_tool"
    fake_content: Dict[str, Any] = {
        "description": "Smoke fake tool for generator testing.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The smoke query."},
                "limit": {"type": "integer", "description": "Max rows."},
            },
            "required": ["query"],
        },
    }
    native_tool_schemas.NATIVE_TOOL_CONTENT[fake_name] = fake_content
    try:
        registry = tool_schemas._get_tool_registry()
        schema = registry[fake_name]
        assert schema == {
            "type": "function",
            "function": {
                "name": fake_name,
                "description": fake_content["description"],
                "parameters": fake_content["parameters"],
            },
        }
        built = build_executor_tool_schemas([fake_name])
        assert built == [schema]
        built_native = build_tool_schemas([fake_name])
        assert built_native[0] == schema
        assert built_native[-1] is tool_schemas.SUBMIT_FINAL_ANSWER_SCHEMA
    finally:
        del native_tool_schemas.NATIVE_TOOL_CONTENT[fake_name]


def test_drift_merged_entries_track_impl_truth() -> None:
    """Drift-merge lock (2026-09-24): entries merged to impl truth must not regress.

    Fully merged tools must expose exactly the impl parameter key set; partially
    merged tools keep only the documented intentional narrowings. Defaults stated
    in native content must equal the defaults the handler actually applies.
    """
    from tool_box import native_tool_schemas
    from tool_box.tools_impl import (
        graph_rag_tool,
        manuscript_writer_tool,
        phagescope_research_tool,
        sequence_fetch_tool,
        url_fetch_tool,
        web_search_tool,
    )

    content = native_tool_schemas.NATIVE_TOOL_CONTENT

    def impl_props(tool: Dict[str, Any]) -> Dict[str, Any]:
        schema = tool.get("parameters_schema") or tool.get("parameters") or {}
        return schema.get("properties", {})

    def native_props(name: str) -> Dict[str, Any]:
        return content[name]["parameters"]["properties"]

    # Fully merged: parameter key sets identical to impl.
    for name, tool in [
        ("web_search", web_search_tool),
        ("sequence_fetch", sequence_fetch_tool),
        ("url_fetch", url_fetch_tool),
        ("graph_rag", graph_rag_tool),
    ]:
        assert set(native_props(name)) == set(impl_props(tool)), name
        for param, spec in impl_props(tool).items():
            if "default" in spec:
                assert native_props(name)[param].get("default") == spec["default"], (name, param)

    # graph_rag: the phantom `mode` param (silently dropped by the handler) stays gone.
    assert "mode" not in native_props("graph_rag")

    # Partially merged: only the documented intentional narrowings may diverge.
    intentional_gaps = {
        "phagescope_research": ({"max_rows", "top_n"}, set()),
        "manuscript_writer": (
            {
                "generation_model",
                "evaluation_model",
                "merge_model",
                "generation_provider",
                "evaluation_provider",
                "merge_provider",
            },
            {"session_id"},
        ),
    }
    for name, tool in [
        ("phagescope_research", phagescope_research_tool),
        ("manuscript_writer", manuscript_writer_tool),
    ]:
        missing, extra = intentional_gaps[name]
        impl_keys = set(impl_props(tool))
        native_keys = set(native_props(name))
        assert impl_keys - native_keys == missing, name
        assert native_keys - impl_keys == extra, name
        for param in impl_keys & native_keys:
            impl_spec = impl_props(tool)[param]
            native_spec = native_props(name)[param]
            if "enum" in impl_spec:
                assert set(impl_spec["enum"]) <= set(native_spec.get("enum", [])), (name, param)
            if "default" in impl_spec:
                assert native_spec.get("default") == impl_spec["default"], (name, param)

    # phagescope_research: impl's primary action stays reachable from the native path.
    assert "deep_profile" in native_props("phagescope_research")["action"]["enum"]

    # code_executor: no guidance pointing at bio_tools (never available on this platform).
    assert "bio_tools" not in content["code_executor"]["description"]
