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
from .tools_impl import (
    claude_code_tool,
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
]

# Tools that need special field mapping
_CUSTOM_TOOLS: List[Dict[str, Any]] = [
    {
        "name": claude_code_tool["name"],
        "description": claude_code_tool["description"],
        "category": "execution",
        "parameters_schema": claude_code_tool["parameters"],
        "handler": claude_code_tool["handler"],
        "tags": ["code", "execution", "claude", "local"],
        "examples": [
            "Train a machine learning model on data/code_task/train.csv",
            "Analyze all files in data/code_task and provide a summary",
            "Write and execute a Python script to process CSV files",
        ],
    },
]


def register_all_tools() -> None:
    """Register every built-in tool from the declarative definitions."""
    for tool_def in _STANDARD_TOOLS:
        register_tool(
            name=tool_def["name"],
            description=tool_def["description"],
            category=tool_def["category"],
            parameters_schema=tool_def["parameters_schema"],
            handler=tool_def["handler"],
            tags=tool_def.get("tags", []),
            examples=tool_def.get("examples", []),
        )

    for tool_def in _CUSTOM_TOOLS:
        register_tool(
            name=tool_def["name"],
            description=tool_def["description"],
            category=tool_def["category"],
            parameters_schema=tool_def["parameters_schema"],
            handler=tool_def["handler"],
            tags=tool_def.get("tags", []),
            examples=tool_def.get("examples", []),
        )

    logger.info(
        "Registered %d built-in tools",
        len(_STANDARD_TOOLS) + len(_CUSTOM_TOOLS),
    )
