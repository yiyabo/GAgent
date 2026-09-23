"""
Native tool calling schemas for DeepThink agent.

Thin generation layer: the per-tool content (description + JSON-schema
parameters) lives in ``tool_box/native_tool_schemas.py`` (single home for the
native path); this module only wraps that content into OpenAI-compatible
function-calling envelopes and keeps the dynamic pieces:

- ``bio_tools``: tool_name enum and description are built from
  ``tool_box/bio_tools/tools_config.json`` at import time (intentional).
- ``verify_task``: app-layer tool with no tools_impl base dict; its content
  stays here next to the generator.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def code_mode_enabled() -> bool:
    """Whether programmatic tool calling (``execute_code``) is offered to the LLM.

    Mirrors ``CODE_MODE_ENABLED`` in ``tool_box/tools_impl/execute_code/config.py``;
    read directly from env here because this module must never import tool_box
    at module import time (circular import guard, see ``_build_registry``).
    """
    return os.environ.get("CODE_MODE_ENABLED", "").strip() == "1"


def _build_execute_code_description(content: Dict[str, Any]) -> str:
    """Static base + the dynamic per-allowlist signature list (teaching surface)."""
    base = str(content["description"])
    try:
        from tool_box.tools_impl.execute_code.config import allowed_tools
        from tool_box.tools_impl.execute_code.stub_gen import signature_lines

        lines = signature_lines(allowed_tools())
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("execute_code signature list unavailable: %s", exc)
        lines = []
    if not lines:
        return base + " (none resolved — check CODE_MODE_ALLOWED_TOOLS)"
    return base + "\n" + "\n".join(f"  {line}" for line in lines)


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_BIO_TOOLS_CONFIG_PATH = _PROJECT_ROOT / "tool_box" / "bio_tools" / "tools_config.json"
_BIO_TOOLS_FALLBACK = ["seqkit", "blast", "prodigal", "hmmer", "checkv"]


def _load_bio_tool_names() -> List[str]:
    """Load bio tool names from tools_config.json with safe fallback."""
    try:
        if not _BIO_TOOLS_CONFIG_PATH.exists():
            return list(_BIO_TOOLS_FALLBACK)
        config = json.loads(_BIO_TOOLS_CONFIG_PATH.read_text(encoding="utf-8"))
        names = [str(name).strip() for name in config.keys() if str(name).strip()]
        if not names:
            return list(_BIO_TOOLS_FALLBACK)
        return sorted(set(names))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to load bio tools config for schema enum: %s", exc)
        return list(_BIO_TOOLS_FALLBACK)


_BIO_TOOL_NAMES = _load_bio_tool_names()


def _build_bio_tools_schema_description() -> str:
    names_text = ", ".join(_BIO_TOOL_NAMES)
    return (
        "PREFERRED for bioinformatics: Docker-based tools for FASTA/FASTQ/sequence "
        "analysis. Tool list is synced from tools_config.json. "
        f"Available tools: {names_text}. "
        "Use operation='help' first to inspect exact operations and parameters. "
        "If the user provides inline sequence text instead of a file, pass it via "
        "sequence_text and let bio_tools convert it to FASTA safely. "
        "For heavy runs, set background=true to submit asynchronously and query "
        "later with operation='job_status' and job_id. "
        "Do not use background mode for quick checks that must return immediately."
    )


def _function_schema(name: str, description: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    """Wrap content into the OpenAI-compatible function-calling envelope."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


def _bio_tools_parameters(content: Dict[str, Any]) -> Dict[str, Any]:
    """Static bio_tools shell with the tools_config.json enum injected."""
    parameters = copy.deepcopy(content["parameters"])
    tool_name = parameters["properties"]["tool_name"]
    # Rebuild preserving the original key order (type, enum, description).
    rebuilt: Dict[str, Any] = {}
    for key, value in tool_name.items():
        rebuilt[key] = value
        if key == "type":
            rebuilt["enum"] = list(_BIO_TOOL_NAMES)
    parameters["properties"]["tool_name"] = rebuilt
    return parameters


# app-layer tool with no tools_impl base dict — content intentionally kept here.
_VERIFY_TASK_DESCRIPTION = (
    "Verify whether a completed task actually produced correct outputs. "
    "Runs deterministic file/data checks (file_exists, file_nonempty, "
    "glob_count_at_least, text_contains, json_field_equals, "
    "json_field_at_least, pdb_residue_present). "
    "IMPORTANT: You MUST pass verification_criteria with concrete check "
    "strings — without them the verifier will skip and return no useful result. "
    "Example criteria: ['file_exists:/data/output.csv', "
    "'file_nonempty:/data/output.csv', "
    "'glob_count_at_least:/results/*.png:3']."
)

_VERIFY_TASK_PARAMETERS: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "task_id": {
            "type": "integer",
            "description": "The task ID to verify within the current plan.",
        },
        "verification_criteria": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "List of shorthand check strings. Formats: "
                "'file_exists:<path>', 'file_nonempty:<path>', "
                "'glob_count_at_least:<glob>:<min_count>', "
                "'text_contains:<path>:<pattern>', "
                "'json_field_equals:<path>:<key_path>:<expected>', "
                "'json_field_at_least:<path>:<key_path>:<min_value>', "
                "'pdb_residue_present:<path>:<residue>'. "
                "Without these, verification will be skipped."
            ),
        },
    },
    "required": ["task_id"],
}


def _build_registry() -> Dict[str, Dict[str, Any]]:
    """Assemble TOOL_REGISTRY in the historical insertion order.

    The native content import is deferred to first use: importing
    ``tool_box.native_tool_schemas`` eagerly would trigger
    ``tool_box/__init__`` → ``tool_registry`` → ``code_executor`` →
    ``app.services.plans.plan_executor`` → this module (circular).
    """
    from tool_box.native_tool_schemas import NATIVE_TOOL_CONTENT

    registry: Dict[str, Dict[str, Any]] = {}
    for name, content in NATIVE_TOOL_CONTENT.items():
        if name == "execute_code":
            # Code mode is env-gated: unless CODE_MODE_ENABLED=1 the entry is
            # invisible to every offer path and the golden master stays exact.
            if not code_mode_enabled():
                continue
            registry[name] = _function_schema(
                name,
                _build_execute_code_description(content),
                content["parameters"],
            )
        elif name == "bio_tools":
            registry[name] = _function_schema(
                name,
                _build_bio_tools_schema_description(),
                _bio_tools_parameters(content),
            )
        else:
            registry[name] = _function_schema(
                name,
                content["description"],
                content["parameters"],
            )
    registry["verify_task"] = _function_schema(
        "verify_task",
        _VERIFY_TASK_DESCRIPTION,
        _VERIFY_TASK_PARAMETERS,
    )
    return registry


_TOOL_REGISTRY_CACHE: Optional[Dict[str, Dict[str, Any]]] = None


def _get_tool_registry() -> Dict[str, Dict[str, Any]]:
    global _TOOL_REGISTRY_CACHE
    if _TOOL_REGISTRY_CACHE is None:
        _TOOL_REGISTRY_CACHE = _build_registry()
    return _TOOL_REGISTRY_CACHE


def __getattr__(name: str) -> Any:
    # PEP 562: lazily build TOOL_REGISTRY on first access so this module never
    # imports tool_box at module import time (circular import guard).
    if name == "TOOL_REGISTRY":
        return _get_tool_registry()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


SUBMIT_FINAL_ANSWER_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "submit_final_answer",
        "description": (
            "Call this tool ONLY when you have gathered enough information and are "
            "ready to provide the final comprehensive answer to the user. "
            "Do NOT call this prematurely - use other tools first to gather information."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "answer": {
                    "type": "string",
                    "description": "The comprehensive final answer, in Markdown format.",
                },
                "confidence": {
                    "type": "number",
                    "description": "Confidence score from 0.0 to 1.0.",
                },
                "deliverables": {
                    "type": "array",
                    "description": (
                        "Optional files or artifacts actually produced or saved during this turn. "
                        "Include this when the user asked for a saved file, report, figure, or Deliverables output."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Path to the generated artifact.",
                            },
                            "description": {
                                "type": "string",
                                "description": "Short description of the generated artifact.",
                            },
                        },
                        "required": ["path"],
                    },
                },
                "evidence_sources": {
                    "type": "array",
                    "description": (
                        "Optional files, URLs, manifests, or tool outputs that materially support the answer. "
                        "Use this for evidence-backed synthesis over plan/task outputs."
                    ),
                    "items": {"type": "string"},
                },
            },
            "required": ["answer"],
        },
    },
}


# Tools available to PlanExecutor for task execution.
EXECUTOR_AVAILABLE_TOOLS: List[str] = [
    "web_search",
    "sequence_fetch",
    "url_fetch",
    "bio_tools",
    "scientific_figure_generator",
    "code_executor",
    "graph_rag",
    "document_reader",
    "vision_reader",
    "phagescope",
    "phagescope_research",
    "literature_pipeline",
    "review_pack_writer",
    "manuscript_writer",
    "deliverable_submit",
    "file_operations",
    "terminal_session",
    "result_interpreter",
]


def build_tool_schemas(available_tools: List[str]) -> List[Dict[str, Any]]:
    """Build the tools payload for native tool calling from available tool names."""
    registry = _get_tool_registry()
    names = list(available_tools)
    if code_mode_enabled() and "execute_code" not in names and "execute_code" in registry:
        # Code-mode offer gate (on): the schema is offered even when the
        # caller's static tool pool predates the flag. Off: registry has no
        # execute_code entry at all, so nothing can leak through.
        names.append("execute_code")
    schemas = []
    for name in names:
        schema = registry.get(name)
        if schema is not None:
            schemas.append(schema)
    schemas.append(SUBMIT_FINAL_ANSWER_SCHEMA)
    return schemas


def build_executor_tool_schemas(available_tools: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Build tool schemas for PlanExecutor, excluding submit_final_answer.

    Unlike build_tool_schemas (used by DeepThink), this does NOT append
    the submit_final_answer termination tool, which is not applicable
    to the plan execution loop.
    """
    registry = _get_tool_registry()
    tools = available_tools if available_tools is not None else EXECUTOR_AVAILABLE_TOOLS
    names = list(tools)
    if (
        available_tools is None
        and code_mode_enabled()
        and "execute_code" not in names
        and "execute_code" in registry
    ):
        # PlanExecutor's default offer pool gains execute_code only under the
        # flag; EXECUTOR_AVAILABLE_TOOLS itself stays static (golden master).
        names.append("execute_code")
    schemas = []
    for name in names:
        schema = registry.get(name)
        if schema is not None:
            schemas.append(schema)
    return schemas
