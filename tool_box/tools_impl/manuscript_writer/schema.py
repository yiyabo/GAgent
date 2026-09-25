"""Tool registry schema for ``manuscript_writer``.

The parameters schema is intentionally a full copy that drifts from the
narrowed hand-written native-call schema in ``app/services/native_tool_schemas``
(kept in sync deliberately, not auto-generated). The handler reference makes
this module import ``pipeline``, so it must stay last in the package import
order.
"""

from __future__ import annotations

from .config import (
    _DEFAULT_MAX_CONTEXT_BYTES,
    _DEFAULT_MAX_REVISIONS,
    _DEFAULT_THRESHOLD,
)
from .pipeline import manuscript_writer_handler

manuscript_writer_tool = {
    "name": "manuscript_writer",
    "description": (
        "Generate a research manuscript with staged section drafting, evaluation, and merge. "
        "Uses the default LLM provider unless overridden."
    ),
    "category": "document_writing",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "Manuscript goal or writing request.",
            },
            "output_path": {
                "type": "string",
                "description": "Output file path for the final manuscript (project-relative).",
            },
            "context_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of context file paths to ground the draft.",
            },
            "analysis_path": {
                "type": "string",
                "description": "Optional path for analysis memo (project-relative).",
            },
            "sections": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Section list (default: abstract/introduction/method/experiment/result/discussion/conclusion/references).",
            },
            "article_mode": {
                "type": "string",
                "enum": ["auto", "review", "research"],
                "description": "Optional article mode override. Use review to force review/synthesis behavior, research to force original-study behavior, or auto to infer from the task.",
                "default": "auto",
            },
            "max_revisions": {
                "type": "integer",
                "description": "Max revision attempts per section.",
                "default": _DEFAULT_MAX_REVISIONS,
            },
            "evaluation_threshold": {
                "type": "number",
                "description": "Pass threshold for average evaluation score (0-1).",
                "default": _DEFAULT_THRESHOLD,
            },
            "max_context_bytes": {
                "type": "integer",
                "description": "Per-file max bytes to read into context.",
                "default": _DEFAULT_MAX_CONTEXT_BYTES,
            },
            "generation_model": {
                "type": "string",
                "description": "Optional model name (or env var key) for generation.",
            },
            "evaluation_model": {
                "type": "string",
                "description": "Optional model name (or env var key) for evaluation.",
            },
            "merge_model": {
                "type": "string",
                "description": "Optional model name (or env var key) for final merge rewrite.",
            },
            "generation_provider": {
                "type": "string",
                "description": "Optional provider override for generation (e.g., qwen, glm).",
            },
            "evaluation_provider": {
                "type": "string",
                "description": "Optional provider override for evaluation (e.g., qwen, glm).",
            },
            "merge_provider": {
                "type": "string",
                "description": "Optional provider override for merge (e.g., qwen, glm).",
            },
            "keep_workspace": {
                "type": "boolean",
                "description": "Keep intermediate drafts/reviews workspace for audit/debugging.",
                "default": False,
            },
            "draft_only": {
                "type": "boolean",
                "description": "Assemble a lightweight local draft without the full staged evaluation and polish pipeline.",
                "default": False,
            },
        },
        "required": ["task", "output_path"],
    },
    "handler": manuscript_writer_handler,
    "tags": ["writing", "manuscript", "evaluation", "qwen"],
    "examples": [
        "Generate a staged manuscript using data/examples/outline.txt and save to runtime/session_x/shared/draft.md",
    ],
}
