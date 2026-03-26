"""
Native tool calling schemas for DeepThink agent.

Defines JSON Schema specifications for all available tools, following the
OpenAI-compatible function calling format used by Qwen, OpenAI, and Kimi.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


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


def build_tool_schemas(available_tools: List[str]) -> List[Dict[str, Any]]:
    """Build the tools payload for native tool calling from available tool names."""
    schemas = []
    for name in available_tools:
        schema = TOOL_REGISTRY.get(name)
        if schema is not None:
            schemas.append(schema)
    schemas.append(SUBMIT_FINAL_ANSWER_SCHEMA)
    return schemas


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
            },
            "required": ["answer"],
        },
    },
}


TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {
    "web_search": {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Broad web search via Alibaba DashScope Responses API (built-in web_search tool). "
                "Use for web-based queries only, NOT for local files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query.",
                    },
                    "queries": {
                        "type": "array",
                        "description": "Optional focused subqueries for parallel search on broad comparison tasks.",
                        "items": {"type": "string"},
                        "minItems": 2,
                        "maxItems": 6,
                    },
                },
                "required": ["query"],
            },
        },
    },
    "sequence_fetch": {
        "type": "function",
        "function": {
            "name": "sequence_fetch",
            "description": (
                "Deterministic accession-to-FASTA downloader with strict domain allowlist. "
                "Use this when the user asks to download FASTA by accession IDs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "accession": {
                        "type": "string",
                        "description": "Single accession ID (mutually exclusive with accessions).",
                    },
                    "accessions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Multiple accession IDs (mutually exclusive with accession).",
                    },
                    "database": {
                        "type": "string",
                        "enum": ["nuccore", "protein"],
                        "description": "NCBI database type.",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["fasta"],
                        "description": "Output format (FASTA only).",
                    },
                    "session_id": {
                        "type": "string",
                        "description": "Optional session id for session-scoped output storage.",
                    },
                    "output_name": {
                        "type": "string",
                        "description": "Optional output filename.",
                    },
                    "timeout_sec": {
                        "type": "number",
                        "description": "Network timeout in seconds.",
                    },
                    "max_bytes": {
                        "type": "integer",
                        "description": "Maximum response payload size in bytes.",
                    },
                },
                "anyOf": [
                    {"required": ["accession"]},
                    {"required": ["accessions"]},
                ],
            },
        },
    },
    "file_operations": {
        "type": "function",
        "function": {
            "name": "file_operations",
            "description": "File system operations: list directories, read/write files, copy/move/delete.",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["list", "read", "write", "copy", "move", "delete"],
                        "description": "The file operation to perform.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the target file or directory.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write (only for write operation).",
                    },
                    "destination": {
                        "type": "string",
                        "description": "Destination path (only for copy/move operations).",
                    },
                },
                "required": ["operation", "path"],
            },
        },
    },
    "claude_code": {
        "type": "function",
        "function": {
            "name": "claude_code",
            "description": (
                "Execute Python/shell code. FALLBACK TOOL: Use this ONLY when bio_tools "
                "cannot handle the task. For standard bioinformatics tasks, ALWAYS try "
                "bio_tools first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Description of the code task to execute.",
                    },
                },
                "required": ["task"],
            },
        },
    },
    "graph_rag": {
        "type": "function",
        "function": {
            "name": "graph_rag",
            "description": "Query a knowledge graph for structured information retrieval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The knowledge query.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["global", "local", "hybrid"],
                        "description": "Search mode. Default: hybrid.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    "document_reader": {
        "type": "function",
        "function": {
            "name": "document_reader",
            "description": "Read local documents with format-aware parsing (.docx, .pdf, .txt).",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["read_any", "read_pdf", "read_text"],
                        "description": "Reading operation type.",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the document file.",
                    },
                },
                "required": ["operation", "file_path"],
            },
        },
    },
    "vision_reader": {
        "type": "function",
        "function": {
            "name": "vision_reader",
            "description": "Read PDFs and images using a vision model. For visual OCR, figures, and equations only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["read_pdf", "read_image", "ocr_page"],
                        "description": "Vision operation type.",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the image or PDF file.",
                    },
                },
                "required": ["operation", "file_path"],
            },
        },
    },
    "bio_tools": {
        "type": "function",
        "function": {
            "name": "bio_tools",
            "description": _build_bio_tools_schema_description(),
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_name": {
                        "type": "string",
                        "enum": _BIO_TOOL_NAMES,
                        "description": "The bioinformatics tool to run.",
                    },
                    "operation": {
                        "type": "string",
                        "description": (
                            "Operation to perform (e.g., stats, grep, predict, help). "
                            "Use 'job_status' to query a submitted background job."
                        ),
                    },
                    "input_file": {
                        "type": "string",
                        "description": "Absolute path to the input file.",
                    },
                    "sequence_text": {
                        "type": "string",
                        "description": (
                            "Inline FASTA or raw sequence text. Use this when no input file is "
                            "available. Mutually exclusive with input_file."
                        ),
                    },
                    "output_file": {
                        "type": "string",
                        "description": "Output filename or directory fragment.",
                    },
                    "params": {
                        "type": "object",
                        "description": "Additional tool-specific parameters.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": (
                            "Execution timeout in seconds. <=0 disables execution timeout "
                            "(for long-running tools)."
                        ),
                    },
                    "background": {
                        "type": "boolean",
                        "description": (
                            "If true, submit bio_tools execution as a background job and "
                            "return immediately with job_id. Recommended only for long-running "
                            "operations that do not need immediate in-turn output."
                        ),
                    },
                    "job_id": {
                        "type": "string",
                        "description": "Background job id used with operation='job_status'.",
                    },
                },
                "required": ["tool_name", "operation"],
            },
        },
    },
    "literature_pipeline": {
        "type": "function",
        "function": {
            "name": "literature_pipeline",
            "description": (
                "Collect a literature evidence pack from PubMed/PMC. "
                "Produces evidence.md, references.bib, and library.jsonl for downstream drafting."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "PubMed search query.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of PubMed records to collect.",
                    },
                    "out_dir": {
                        "type": "string",
                        "description": "Optional project-relative output directory.",
                    },
                    "download_pdfs": {
                        "type": "boolean",
                        "description": "Whether to try downloading PMC PDFs.",
                    },
                    "max_pdfs": {
                        "type": "integer",
                        "description": "Maximum number of PDFs to download when download_pdfs=true.",
                    },
                    "user_agent": {
                        "type": "string",
                        "description": "Optional HTTP user-agent override.",
                    },
                    "proxy": {
                        "type": "string",
                        "description": "Optional HTTP proxy URL.",
                    },
                    "session_id": {
                        "type": "string",
                        "description": "Optional session id for session-scoped output storage.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    "review_pack_writer": {
        "type": "function",
        "function": {
            "name": "review_pack_writer",
            "description": (
                "Generate a literature-backed review draft by first collecting evidence "
                "with literature_pipeline and then drafting with manuscript_writer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "High-level review topic.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Optional explicit PubMed query override.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of PubMed records to collect.",
                    },
                    "download_pdfs": {
                        "type": "boolean",
                        "description": "Whether to try downloading PMC PDFs.",
                    },
                    "max_pdfs": {
                        "type": "integer",
                        "description": "Maximum number of PDFs to download when download_pdfs=true.",
                    },
                    "output_path": {
                        "type": "string",
                        "description": "Optional project-relative output path for the review draft.",
                    },
                    "sections": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional subset of manuscript sections to draft.",
                    },
                    "max_revisions": {
                        "type": "integer",
                        "description": "Maximum revision rounds per section.",
                    },
                    "evaluation_threshold": {
                        "type": "number",
                        "description": "Section evaluation pass threshold from 0 to 1.",
                    },
                    "keep_workspace": {
                        "type": "boolean",
                        "description": "Whether to keep intermediate drafting workspace artifacts.",
                    },
                    "task": {
                        "type": "string",
                        "description": "Optional direct manuscript task override.",
                    },
                    "session_id": {
                        "type": "string",
                        "description": "Optional session id for session-scoped output storage.",
                    },
                },
                "required": ["topic"],
            },
        },
    },
    "phagescope": {
        "type": "function",
        "function": {
            "name": "phagescope",
            "description": (
                "PhageScope cloud platform for phage genome analysis. ASYNC service. "
                "Workflow: submit -> task_list/task_detail -> result. "
                "After submit, report the taskid and tell the user to check status later."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["submit", "task_list", "task_detail", "result", "save_all", "download", "input_check"],
                        "description": "The PhageScope action to perform.",
                    },
                    "userid": {"type": "string", "description": "User ID for the PhageScope platform."},
                    "phageid": {"type": "string", "description": "Single phage ID or accession."},
                    "phageids": {"type": "string", "description": "Comma-separated phage IDs."},
                    "taskid": {
                        "type": "string",
                        "description": (
                            "Numeric PhageScope remote task ID for status/result queries "
                            "(e.g., 37468), not local job ids like act_xxx."
                        ),
                    },
                    "modulelist": {
                        "type": "string",
                        "description": (
                            "Comma-separated submit modules only. For Annotation Pipline, use real submit "
                            "modules such as quality, annotation, host, lifestyle, terminator, taxonomic, "
                            "trna, anticrispr, crispr, arvf, transmembrane. Do not use result/output names "
                            "such as proteins, phage_detail, phagefasta, or tree in submit modulelist."
                        ),
                    },
                    "result_kind": {
                        "type": "string",
                        "enum": ["quality", "proteins", "phage_detail", "modules", "tree", "phagefasta"],
                        "description": "Type of result to retrieve.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    "deeppl": {
        "type": "function",
        "function": {
            "name": "deeppl",
            "description": (
                "DeepPL lifecycle prediction (DNABERT-based). "
                "Actions: help, predict, job_status. "
                "For predict, provide exactly one of input_file or sequence_text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["help", "predict", "job_status"],
                        "description": "DeepPL action to perform.",
                    },
                    "input_file": {
                        "type": "string",
                        "description": "Path to FASTA/raw sequence input file.",
                    },
                    "sequence_text": {
                        "type": "string",
                        "description": "Inline FASTA or raw sequence text.",
                    },
                    "execution_mode": {
                        "type": "string",
                        "enum": ["local", "remote"],
                        "description": "Execution mode for prediction.",
                    },
                    "remote_profile": {
                        "type": "string",
                        "enum": ["gpu", "cpu", "default"],
                        "description": "Remote server profile selector when execution_mode=remote.",
                    },
                    "model_path": {
                        "type": "string",
                        "description": "Model directory path (local/remote).",
                    },
                    "background": {
                        "type": "boolean",
                        "description": "If true, submit prediction in background.",
                    },
                    "job_id": {
                        "type": "string",
                        "description": "Background job id for action='job_status'.",
                    },
                    "session_id": {
                        "type": "string",
                        "description": "Optional session id for runtime-scoped outputs.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    "result_interpreter": {
        "type": "function",
        "function": {
            "name": "result_interpreter",
            "description": (
                "Data analysis and result interpretation tool. Analyzes CSV, TSV, MAT, NPY "
                "data files by generating and executing Python code."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["metadata", "generate", "execute", "analyze"],
                        "description": "Analysis operation. 'analyze' is the recommended full pipeline.",
                    },
                    "file_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Absolute paths to data files.",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Single file path (for metadata operation).",
                    },
                    "task_title": {
                        "type": "string",
                        "description": "Title for the analysis task.",
                    },
                    "task_description": {
                        "type": "string",
                        "description": "Description of what to analyze.",
                    },
                },
                "required": ["operation"],
            },
        },
    },
    "manuscript_writer": {
        "type": "function",
        "function": {
            "name": "manuscript_writer",
            "description": (
                "Write a research manuscript or section with citation-aware drafting, "
                "evaluation, and merge support."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Writing task or manuscript instruction.",
                    },
                    "output_path": {
                        "type": "string",
                        "description": "Project-relative output file path.",
                    },
                    "context_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional supporting context files such as evidence.md or references.bib.",
                    },
                    "analysis_path": {
                        "type": "string",
                        "description": "Optional project-relative analysis memo path.",
                    },
                    "sections": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional subset of manuscript sections to draft.",
                    },
                    "max_revisions": {
                        "type": "integer",
                        "description": "Maximum revision rounds per section.",
                    },
                    "evaluation_threshold": {
                        "type": "number",
                        "description": "Section evaluation pass threshold from 0 to 1.",
                    },
                    "max_context_bytes": {
                        "type": "integer",
                        "description": "Maximum context size loaded from supporting files.",
                    },
                    "session_id": {
                        "type": "string",
                        "description": "Optional session id for session-scoped output storage.",
                    },
                    "keep_workspace": {
                        "type": "boolean",
                        "description": "Whether to keep intermediate drafting workspace artifacts.",
                    },
                },
                "required": ["task", "output_path"],
            },
        },
    },
    "plan_operation": {
        "type": "function",
        "function": {
            "name": "plan_operation",
            "description": (
                "Plan creation and optimization tool. Operations: create, review, optimize, get. "
                "For plan creation, research first with web_search, then create -> review -> optimize."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["create", "review", "optimize", "get"],
                        "description": "Plan operation to perform.",
                    },
                    "title": {"type": "string", "description": "Plan title (for create)."},
                    "description": {"type": "string", "description": "Plan goal description (for create)."},
                    "plan_id": {"type": "integer", "description": "Plan ID (for review/optimize/get)."},
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "instruction": {"type": "string"},
                                "dependencies": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["name", "instruction"],
                        },
                        "description": "Task list (for create).",
                    },
                    "changes": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Optimization changes (for optimize).",
                    },
                },
                "required": ["operation"],
            },
        },
    },
    "terminal_session": {
        "type": "function",
        "function": {
            "name": "terminal_session",
            "description": (
                "Manage interactive terminal sessions (sandbox PTY or remote SSH). "
                "Use 'create' to open a new terminal tied to the current chat session, "
                "'write' to send commands (plain text or base64), "
                "'list' to see active sessions, "
                "'close' to terminate a session, "
                "'replay' / 'audit' for history. "
                "Prefer sandbox mode for local scripts/debugging; use ssh mode to reach "
                "remote servers (e.g. bio-tools GPU node). "
                "For 'write', terminal_id is auto-resolved if omitted — no need to call 'ensure' first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": [
                            "create", "ensure", "list", "close",
                            "write", "resize",
                            "approve", "reject", "pending_approvals",
                            "replay", "audit",
                        ],
                        "description": "Operation to perform on the terminal session.",
                    },
                    "session_id": {
                        "type": "string",
                        "description": "Chat session ID to associate the terminal with.",
                    },
                    "terminal_id": {
                        "type": "string",
                        "description": "Terminal instance UUID (required for write/close/replay/audit).",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["sandbox", "ssh"],
                        "description": "Terminal backend: 'sandbox' for local PTY, 'ssh' for remote.",
                    },
                    "data": {
                        "type": "string",
                        "description": "Command text to send (for write operation).",
                    },
                    "encoding": {
                        "type": "string",
                        "enum": ["utf-8", "base64"],
                        "description": "Encoding of the data field (default utf-8).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max rows to return for audit/replay (default 500).",
                    },
                },
                "required": ["operation"],
            },
        },
    },
    "verify_task": {
        "type": "function",
        "function": {
            "name": "verify_task",
            "description": (
                "Verify whether a completed task actually produced correct outputs. "
                "Runs deterministic file/data checks (file_exists, file_nonempty, "
                "glob_count_at_least, text_contains, json_field_equals, "
                "json_field_at_least, pdb_residue_present). "
                "IMPORTANT: You MUST pass verification_criteria with concrete check "
                "strings — without them the verifier will skip and return no useful result. "
                "Example criteria: ['file_exists:/data/output.csv', "
                "'file_nonempty:/data/output.csv', "
                "'glob_count_at_least:/results/*.png:3']."
            ),
            "parameters": {
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
            },
        },
    },
}
