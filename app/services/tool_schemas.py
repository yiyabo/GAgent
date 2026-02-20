"""
Native tool calling schemas for DeepThink agent.

Defines JSON Schema specifications for all available tools, following the
OpenAI-compatible function calling format used by Qwen, OpenAI, and Kimi.
"""

from __future__ import annotations

from typing import Any, Dict, List


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
            "description": "Search the internet for real-time information. Use for web-based queries only, NOT for local files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query.",
                    },
                },
                "required": ["query"],
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
            "description": (
                "PREFERRED for bioinformatics: Docker-based tools for FASTA/FASTQ/sequence "
                "analysis. Available tools: seqkit (stats/grep/seq/head), blast (blastn/blastp/"
                "makeblastdb), prodigal (predict/meta), hmmer (hmmscan/hmmsearch), checkv "
                "(end_to_end/completeness). Use operation='help' to see tool usage."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_name": {
                        "type": "string",
                        "enum": ["seqkit", "blast", "prodigal", "hmmer", "checkv"],
                        "description": "The bioinformatics tool to run.",
                    },
                    "operation": {
                        "type": "string",
                        "description": "Operation to perform (e.g., stats, grep, predict, help).",
                    },
                    "input_file": {
                        "type": "string",
                        "description": "Absolute path to the input file.",
                    },
                    "params": {
                        "type": "object",
                        "description": "Additional tool-specific parameters.",
                    },
                },
                "required": ["tool_name", "operation"],
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
                    "taskid": {"type": "string", "description": "Task ID for status/result queries."},
                    "modulelist": {"type": "string", "description": "Comma-separated analysis modules."},
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
}
