"""
Explicit deliverables handoff: Agent lists paths to copy into session deliverables/latest.

Used when DELIVERABLES_INGEST_MODE=explicit (see app.config.deliverable_config).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


async def deliverable_submit_handler(
    *,
    publish: bool = True,
    artifacts: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Return a structured payload consumed by DeliverablePublisher.publish_from_tool_result.

    Each artifact: {"path": "<absolute or project path>", "module": "image_tabular"|"code"|...}
    """
    rows = artifacts if isinstance(artifacts, list) else []
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        path_val = row.get("path")
        mod_val = row.get("module")
        if isinstance(path_val, str) and path_val.strip() and isinstance(mod_val, str) and mod_val.strip():
            entry: Dict[str, Any] = {"path": path_val.strip(), "module": mod_val.strip().lower()}
            reason = row.get("reason")
            if isinstance(reason, str) and reason.strip():
                entry["reason"] = reason.strip()
            normalized.append(entry)

    return {
        "success": True,
        "tool": "deliverable_submit",
        "deliverable_submit": {
            "publish": bool(publish),
            "artifacts": normalized,
        },
    }


deliverable_submit_tool = {
    "name": "deliverable_submit",
    "description": (
        "Submit FINAL output files to the session Deliverables panel. "
        "Use this ONLY for publication-ready artifacts:\n"
        "- Visualization plots (PNG/SVG/PDF charts, figures)\n"
        "- Summary tables (final analysis results, NOT raw data)\n"
        "- Manuscripts and reports (LaTeX, Markdown)\n"
        "- Finished code scripts that produced the above\n"
        "Do NOT submit: raw input data, intermediate CSVs, downloaded references, logs.\n"
        "Each artifact requires a path and target module (code, image_tabular, paper, refs, docs)."
    ),
    "category": "deliverables",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "publish": {
                "type": "boolean",
                "description": "If false, no files are copied in this call.",
                "default": True,
            },
            "artifacts": {
                "type": "array",
                "description": "Files to copy into deliverables/latest/<module>/",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Source file path"},
                        "module": {
                            "type": "string",
                            "description": "Target module: code, image_tabular, paper, refs, or docs",
                        },
                        "reason": {"type": "string", "description": "Optional note for audit logs"},
                    },
                    "required": ["path", "module"],
                },
            },
        },
        "required": ["artifacts"],
    },
    "handler": deliverable_submit_handler,
    "tags": ["deliverables", "artifacts", "session"],
    "examples": [
        "deliverable_submit(artifacts=[{path: runtime/session_x/task1/results/plot.png, module: image_tabular}])",
    ],
}
