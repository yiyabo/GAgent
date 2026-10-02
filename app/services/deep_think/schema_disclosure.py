"""Progressive tool-schema disclosure for the native deep_think loop.

Every LLM iteration used to re-send the full ~40KB tools payload (~13k
tokens); a typical run touches 3-5 tools. Policy (decision 2026-09-27):

- iteration 1 sends the FULL payload so the model learns the surface;
- iteration 2+ sends only ``used-so-far ∪ CORE_KEEP ∪ loaded ∪ {load_tool_schema}``;
- ``load_tool_schema`` is a meta tool (same philosophy as ``load_skill``):
  the model pulls any other available tool's schema on demand and the tool
  becomes callable from the next iteration;
- escape hatch: a tool call the trimmed payload cannot serve
  (``tool_not_available`` in dispatch) flips the run back to the full
  payload for the rest of the run.

Env: ``SCHEMA_PROGRESSIVE_ENABLED`` (default ``"1"``; ``"0"`` restores
always-full without a code change).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Set

logger = logging.getLogger(__name__)

META_TOOL_NAME = "load_tool_schema"
SUBMIT_FINAL_ANSWER_NAME = "submit_final_answer"

# Always-on tools for the trimmed payload: the loop's universal verbs. Kept
# tiny on purpose — everything else is one load_tool_schema call away.
CORE_KEEP_TOOLS = ("execute_code", "file_operations", "web_search", "deliverable_submit")
# Conditional core: kept while the run is bound to a plan.
PLAN_BOUND_KEEP_TOOLS = ("plan_operation",)

_META_DESCRIPTION = (
    "Load another tool's schema so you can call it in later iterations. "
    "Call this when the task needs a tool you know exists but is not in your "
    "current tool list (e.g. vision_reader, document_reader, "
    "literature_pipeline, phagescope, result_interpreter, url_fetch). "
    "The tool becomes callable from the next iteration — do not retry the "
    "same step, proceed with the newly loaded tool. "
    "Params: {\"name\": \"<tool name>\"}."
)


def progressive_enabled() -> bool:
    return os.environ.get("SCHEMA_PROGRESSIVE_ENABLED", "1").strip() != "0"


class SchemaDisclosure:
    """Per-run progressive-disclosure state (attached to the agent at setup)."""

    def __init__(self, full_schemas: List[Dict[str, Any]], available_tools: List[str]) -> None:
        self._full = list(full_schemas or [])
        self._available: Set[str] = {str(name) for name in (available_tools or [])}
        self._loaded: Set[str] = set()
        self._force_full = False
        from .runtime_policy import configured_policy
        self.v2 = configured_policy()['schemas']
        self._disclosed: Set[str] = set()
        self.enabled = progressive_enabled() and bool(self._full)

    @property
    def loaded(self) -> Set[str]:
        return set(self._loaded)

    @property
    def force_full_active(self) -> bool:
        return self._force_full

    def meta_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": META_TOOL_NAME,
                "description": _META_DESCRIPTION + (" Available: " + "; ".join(s["function"]["name"]+": "+s["function"].get("description","")[:80] for s in sorted(self._full,key=lambda x:x["function"]["name"])) if self.v2 else ""),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Tool name to load, e.g. vision_reader.",
                        }
                    },
                    "required": ["name"],
                },
            },
        }

    def effective(
        self,
        *,
        iteration: int,
        tools_used: List[str],
        plan_bound: bool = False,
    ) -> List[Dict[str, Any]]:
        """The tools payload for this iteration (see module docstring)."""
        if not self.enabled or self._force_full or (iteration <= 1 and not self.v2):
            return list(self._full)
        keep = set(self._loaded)
        keep.update(str(tool) for tool in (tools_used or []) if tool)
        keep.update(CORE_KEEP_TOOLS)
        if plan_bound:
            keep.update(PLAN_BOUND_KEEP_TOOLS)
        if self.v2:
            keep.add("load_skill")
            self._disclosed.update(keep)
            keep.update(self._disclosed)
        trimmed = [
            schema
            for schema in self._full
            if schema["function"]["name"] in keep
            or schema["function"]["name"] == SUBMIT_FINAL_ANSWER_NAME
        ]
        trimmed.append(self.meta_schema())
        if self.v2:
            from copy import deepcopy
            from tool_box.tools_impl.execute_code.tool import build_description
            trimmed=deepcopy(trimmed)
            for schema in trimmed:
                if schema['function']['name']=='execute_code':
                    schema['function']['description']=build_description(progressive=True)
        logger.info(
            "[SCHEMA_DISCLOSURE] iteration=%s payload_tools=%d (full=%d) keep=%s",
            iteration,
            len(trimmed),
            len(self._full),
            ",".join(sorted(keep))[:200],
        )
        return trimmed

    def record_load(self, name: str) -> Dict[str, Any]:
        """Handle a load_tool_schema call: validate + record for the next round."""
        tool = str(name or "").strip()
        if not tool or tool == META_TOOL_NAME or tool == SUBMIT_FINAL_ANSWER_NAME:
            return {
                "success": False,
                "error": "unknown_tool",
                "summary": f"Cannot load '{name}'.",
            }
        if tool not in self._available:
            return {
                "success": False,
                "error": "unknown_tool",
                "summary": (
                    f"'{tool}' is not an available tool in this session; "
                    "no schema was loaded. Do not retry with the same name."
                ),
            }
        first_load = tool not in self._loaded
        self._loaded.add(tool)
        logger.info("[SCHEMA_DISCLOSURE] loaded tool schema: %s", tool)
        return {
            "success": True,
            "tool": tool,
            "loaded": first_load,
            "schema": next((s for s in self._full if s["function"]["name"]==tool),None),
            "summary": f"'{tool}' is now available; call it directly in your next step.",
        }

    def force_full(self, reason: str) -> None:
        """Flip the run back to the full payload (escape hatch)."""
        if not self._force_full:
            logger.warning("[SCHEMA_DISCLOSURE] restoring FULL tool payload: %s", reason)
        self._force_full = True
