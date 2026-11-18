"""Declarative tool registry — replaces repetitive register_tool() calls.

Each entry in ``TOOL_DEFINITIONS`` maps directly to the keyword arguments of
:func:`tool_box.tools.register_tool`.  Tools whose definition dict uses a
non-standard key (e.g. ``parameters`` instead of ``parameters_schema``) are
handled via the ``overrides`` mechanism.
"""

from __future__ import annotations

import logging
from typing import Any

from .bio_tools import bio_tools_tool
from .tools import register_tool
from .tools_impl import (
    code_executor_tool,
    delegate_task_tool,
    document_reader_tool,
    execute_code_tool,
    file_operations_tool,
    generate_experiment_card_tool,
    graph_rag_tool,
    lightrag_query_tool,
    literature_pipeline_tool,
    load_skill_tool,
    manuscript_writer_tool,
    memory_operation_tool,
    ncbi_search_tool,
    paper_replication_tool,
    phagescope_data_tool,
    phagescope_research_tool,
    phagescope_tool,
    plan_operation_tool,
    result_interpreter_tool,
    review_pack_writer_tool,
    scientific_figure_generator_tool,
    sequence_fetch_tool,
    terminal_session_tool,
    url_fetch_tool,
    vision_reader_tool,
    web_search_tool,
)
from .tools_impl.delegate_task import delegate_task_backend, delegate_task_enabled
from .tools_impl.deliverable_submit import deliverable_submit_tool
from .tools_impl.phagescope_data import phagescope_data_enabled

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Orchestration metadata for tools.
# Keys match ToolDefinition fields added in Phase 1.1.
# Only tools that differ from the conservative defaults need entries here;
# tools not listed get is_read_only=False, is_concurrent_safe=False, etc.
# ---------------------------------------------------------------------------
_TOOL_METADATA: dict[str, dict[str, Any]] = {
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
    "lightrag_query": {
        "is_read_only": True,
        "is_concurrent_safe": True,
        "search_hint": "lightrag knowledge graph rag literature corpus entity relation shard",
    },
    "graph_rag": {
        "is_read_only": True,
        "is_concurrent_safe": True,
        "search_hint": "legacy small triples knowledge graph rag entity relation",
    },
    "sequence_fetch": {
        "is_read_only": True,
        "is_concurrent_safe": True,
        "search_hint": "ncbi genbank sequence fasta accession fetch",
    },
    "ncbi_search": {
        "is_read_only": True,
        "is_concurrent_safe": True,
        "search_hint": "ncbi entrez search accession summary taxonomy lineage assembly genome metadata",
    },
    "url_fetch": {
        "search_hint": "download public url link file http https fetch",
    },
    "memory_operation": {
        # Not read-only: save/update/forget mutate the user's memory store.
        "search_hint": "memory remember recall preference fact forget long-term user profile",
    },
    "load_skill": {
        "is_read_only": True,
        "is_concurrent_safe": True,
        "search_hint": "skill load instructions playbook guide capability progressive disclosure",
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
        "search_hint": "phage bacteriophage gateway catalog protein host family structure sequence annotation slurm submit status results",
    },
    "phagescope_data": {
        "search_hint": "phagescope v2 database phage genome protein family host taxonomy structure fasta faa gff3 materialize cohort",
    },
    "phagescope_research": {
        "search_hint": "phagescope local dataset host taxonomy machine learning audit split",
    },
    "bio_tools": {
        "search_hint": "bioinformatics blast assembly alignment annotation",
    },
    "code_executor": {
        "search_hint": "python code execute run script data analysis plot",
    },
    "execute_code": {
        "search_hint": "python programmatic tool calling loop batch kernel code mode",
    },
    "delegate_task": {
        "search_hint": "delegate subagent isolated long horizon workflow hand off goal",
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
        "is_read_only": True,
        "search_hint": "plan create review optimize task decompose",
    },
    "deliverable_submit": {
        "search_hint": "deliverable artifact submit publish output",
    },
    "scientific_figure_generator": {
        "is_concurrent_safe": True,
        "search_hint": "scientific figure visualization plot chart heatmap composite legend qa provenance",
    },
}


def get_tool_orchestration_metadata(tool_name: str) -> dict[str, Any]:
    """Return orchestration metadata for *tool_name* from the declarative registry.

    This is the public API for accessing ``_TOOL_METADATA`` from outside this
    module (e.g. in ``deep_think_agent.py`` when the live tool registry is not
    yet populated, such as during unit tests).  Returns an empty dict for
    unknown tools.
    """
    return _TOOL_METADATA.get(tool_name, {})


# Standard tools follow the common schema:
#   name, description, category, parameters_schema, handler, tags?, examples?
_STANDARD_TOOLS: list[dict[str, Any]] = [
    web_search_tool,
    literature_pipeline_tool,
    review_pack_writer_tool,
    file_operations_tool,
    document_reader_tool,
    vision_reader_tool,
    paper_replication_tool,
    generate_experiment_card_tool,
    lightrag_query_tool,
    graph_rag_tool,
    manuscript_writer_tool,
    phagescope_tool,
    phagescope_data_tool,
    phagescope_research_tool,
    sequence_fetch_tool,
    ncbi_search_tool,
    url_fetch_tool,
    terminal_session_tool,
    bio_tools_tool,
    result_interpreter_tool,
    scientific_figure_generator_tool,
    plan_operation_tool,
    deliverable_submit_tool,
    execute_code_tool,
    delegate_task_tool,
    load_skill_tool,
    memory_operation_tool,
]

# Tools that need special field mapping
_CUSTOM_TOOLS: list[dict[str, Any]] = [
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


def _extract_registration_kwargs(tool_def: dict[str, Any]) -> dict[str, Any]:
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
        "is_concurrent_safe": tool_def.get(
            "is_concurrent_safe", meta.get("is_concurrent_safe", _dynamic_concurrent_safe(name))
        ),
        "is_destructive": tool_def.get("is_destructive", meta.get("is_destructive", False)),
        "search_hint": tool_def.get("search_hint", meta.get("search_hint", "")),
    }


def _dynamic_concurrent_safe(name: str) -> bool:
    """Concurrency that depends on runtime config rather than static metadata."""
    if name == "delegate_task":
        # Native in-process sub-agents are isolated per child workspace; the
        # legacy CLI lane drives one external session at a time.
        return delegate_task_backend() == "native"
    return False


# Env-gated definitions: absent from the runtime registry unless the flag is on,
# so they cannot be discovered or dispatched while disabled.
_GATED_TOOLS = {
    "delegate_task": delegate_task_enabled,
    "phagescope_data": phagescope_data_enabled,
}


def _is_enabled(tool_def: dict[str, Any]) -> bool:
    gate = _GATED_TOOLS.get(str(tool_def.get("name") or ""))
    return True if gate is None else bool(gate())


def register_all_tools() -> None:
    """Register every built-in tool from the declarative definitions."""
    enabled: list[dict[str, Any]] = []
    skipped: list[str] = []
    for tool_def in _STANDARD_TOOLS + _CUSTOM_TOOLS:
        if _is_enabled(tool_def):
            enabled.append(tool_def)
        else:
            skipped.append(str(tool_def["name"]))

    for tool_def in enabled:
        register_tool(**_extract_registration_kwargs(tool_def))

    logger.info("Registered %d built-in tools", len(enabled))
    if skipped:
        logger.info("Skipped env-gated tools: %s", ", ".join(skipped))
