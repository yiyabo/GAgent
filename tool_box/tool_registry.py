"""Declarative tool registry — replaces repetitive register_tool() calls.

Each entry in ``TOOL_DEFINITIONS`` maps directly to the keyword arguments of
:func:`tool_box.tools.register_tool`.  Tools whose definition dict uses a
non-standard key (e.g. ``parameters`` instead of ``parameters_schema``) are
handled via the ``overrides`` mechanism.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .tools import register_tool
from .tools_impl.deliverable_submit import deliverable_submit_tool
from .tools_impl import (
    code_executor_tool,
    database_query_tool,
    deeppl_tool,
    document_reader_tool,
    file_operations_tool,
    generate_experiment_card_tool,
    graph_rag_tool,
    internal_api_tool,
    literature_pipeline_tool,
    manuscript_writer_tool,
    paper_replication_tool,
    phagescope_tool,
    plan_operation_tool,
    result_interpreter_tool,
    review_pack_writer_tool,
    sequence_fetch_tool,
    terminal_session_tool,
    vision_reader_tool,
    web_search_tool,
)
from .bio_tools import bio_tools_tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Orchestration metadata for tools.
# Keys match ToolDefinition fields added in Phase 1.1.
# Only tools that differ from the conservative defaults need entries here;
# tools not listed get is_read_only=False, is_concurrent_safe=False, etc.
# ---------------------------------------------------------------------------
_TOOL_METADATA: Dict[str, Dict[str, Any]] = {
    # --- read-only & concurrent-safe: pure information retrieval ---
    "web_search": {
        "is_read_only": True,
        "is_concurrent_safe": True,
        "search_hint": "search web internet query google perplexity",
    },
    "literature_pipeline": {
        "is_read_only": True,
        "is_concurrent_safe": True,
        "search_hint": "paper literature pubmed scholar citation",
    },
    "document_reader": {
        "is_read_only": True,
        "is_concurrent_safe": True,
        "search_hint": "read pdf docx document parse extract",
    },
    "vision_reader": {
        "is_read_only": True,
        "is_concurrent_safe": True,
        "search_hint": "image screenshot ocr visual analyze picture",
    },
    "graph_rag": {
        "is_read_only": True,
        "is_concurrent_safe": True,
        "search_hint": "knowledge graph rag entity relation",
    },
    "sequence_fetch": {
        "is_read_only": True,
        "is_concurrent_safe": True,
        "search_hint": "ncbi genbank sequence fasta accession fetch",
    },
    "database_query": {
        # NOT is_read_only=True: the "execute" operation runs INSERT/UPDATE/DELETE SQL.
        # Marking the whole tool as read-only would misclassify execute calls as
        # observation-only in execute_task probe-loop detection.
        "is_concurrent_safe": True,
        "search_hint": "sql database query select table",
    },
    "deeppl": {
        "is_concurrent_safe": True,
        "search_hint": "phage lifestyle prediction temperate lytic",
    },
    # --- read-only but NOT concurrent-safe (heavyweight / stateful) ---
    "result_interpreter": {
        # NOT is_read_only=True: the "execute" operation actually runs generated code.
        # Marking the whole tool as read-only would misclassify execute calls as
        # observation-only in execute_task probe-loop detection.
        "search_hint": "interpret analyze result data summary",
    },
    # --- mutating tools (default: not concurrent-safe) ---
    "file_operations": {
        "search_hint": "file read write copy move delete list",
    },
    "phagescope": {
        "search_hint": "phage bacteriophage annotation pipeline submit",
    },
    "bio_tools": {
        "search_hint": "bioinformatics blast assembly alignment annotation",
    },
    "code_executor": {
        "search_hint": "python code execute run script data analysis plot",
    },
    "terminal_session": {
        "is_destructive": True,
        "search_hint": "ssh terminal shell remote command server",
    },
    "manuscript_writer": {
        "search_hint": "manuscript paper write draft nature science",
    },
    "review_pack_writer": {
        "search_hint": "review response rebuttal referee comment",
    },
    "plan_operation": {
        "search_hint": "plan create review optimize task decompose",
    },
    "deliverable_submit": {
        "search_hint": "deliverable artifact submit publish output",
    },
}

def get_tool_orchestration_metadata(tool_name: str) -> Dict[str, Any]:
    """Return orchestration metadata for *tool_name* from the declarative registry.

    This is the public API for accessing ``_TOOL_METADATA`` from outside this
    module (e.g. in ``deep_think_agent.py`` when the live tool registry is not
    yet populated, such as during unit tests).  Returns an empty dict for
    unknown tools.
    """
    return _TOOL_METADATA.get(tool_name, {})


# Standard tools follow the common schema:
#   name, description, category, parameters_schema, handler, tags?, examples?
_STANDARD_TOOLS: List[Dict[str, Any]] = [
    web_search_tool,
    literature_pipeline_tool,
    review_pack_writer_tool,
    file_operations_tool,
    database_query_tool,
    internal_api_tool,
    document_reader_tool,
    vision_reader_tool,
    paper_replication_tool,
    generate_experiment_card_tool,
    graph_rag_tool,
    manuscript_writer_tool,
    phagescope_tool,
    deeppl_tool,
    sequence_fetch_tool,
    terminal_session_tool,
    bio_tools_tool,
    result_interpreter_tool,
    plan_operation_tool,
    deliverable_submit_tool,
]

# Tools that need special field mapping
_CUSTOM_TOOLS: List[Dict[str, Any]] = [
    {
        "name": code_executor_tool["name"],
        "description": code_executor_tool["description"],
        "category": "execution",
        "parameters_schema": code_executor_tool["parameters"],
        "handler": code_executor_tool["handler"],
        "tags": ["code", "execution", "claude", "local"],
        "examples": [
            "Train a machine learning model on data/code_task/train.csv",
            "Analyze all files in data/code_task and provide a summary",
            "Write and execute a Python script to process CSV files",
        ],
    },
]


def _extract_registration_kwargs(tool_def: Dict[str, Any]) -> Dict[str, Any]:
    """Extract keyword arguments for register_tool() from a tool definition dict.

    Orchestration metadata is merged from three sources (highest priority last):
    1. Conservative defaults (all False / empty)
    2. ``_TOOL_METADATA`` lookup by tool name
    3. Inline keys in the tool_def dict itself
    """
    name = tool_def["name"]
    meta = _TOOL_METADATA.get(name, {})
    return {
        "name": name,
        "description": tool_def["description"],
        "category": tool_def["category"],
        "parameters_schema": tool_def["parameters_schema"],
        "handler": tool_def["handler"],
        "tags": tool_def.get("tags", []),
        "examples": tool_def.get("examples", []),
        # orchestration metadata — inline > _TOOL_METADATA > default
        "is_read_only": tool_def.get("is_read_only", meta.get("is_read_only", False)),
        "is_concurrent_safe": tool_def.get("is_concurrent_safe", meta.get("is_concurrent_safe", False)),
        "is_destructive": tool_def.get("is_destructive", meta.get("is_destructive", False)),
        "search_hint": tool_def.get("search_hint", meta.get("search_hint", "")),
    }


def register_all_tools() -> None:
    """Register every built-in tool from the declarative definitions."""
    for tool_def in _STANDARD_TOOLS:
        register_tool(**_extract_registration_kwargs(tool_def))

    for tool_def in _CUSTOM_TOOLS:
        register_tool(**_extract_registration_kwargs(tool_def))

    logger.info(
        "Registered %d built-in tools",
        len(_STANDARD_TOOLS) + len(_CUSTOM_TOOLS),
    )
