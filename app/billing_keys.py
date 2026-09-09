"""Stable billing keys for LLM-bearing Agent capabilities.

A billing key identifies the product capability that caused an upstream LLM
request.  It is intentionally independent from provider, model, and API key.
"""
from __future__ import annotations

from typing import Final

CHAT_MAIN: Final = "chat.main"
AGENT_RUN: Final = "agent.run"
DEEP_THINK_ITERATION: Final = "deep_think.iteration"
DEEP_THINK_FORCED_SYNTHESIS: Final = "deep_think.forced_synthesis"
PLAN_TASK_EXECUTION: Final = "plan.task_execution"
PLAN_DECOMPOSITION: Final = "plan.decomposition"
PLAN_VERIFICATION: Final = "plan.verification"
TOOL_EXECUTION: Final = "tool.execution"
TOOL_CODE_EXECUTOR: Final = "tool.code_executor"
TOOL_MANUSCRIPT_WRITER: Final = "tool.manuscript_writer"
TOOL_MANUSCRIPT_WRITER_SECTION: Final = "tool.manuscript_writer.section"
TOOL_MANUSCRIPT_WRITER_EVALUATION: Final = "tool.manuscript_writer.evaluation"
TOOL_MANUSCRIPT_WRITER_MERGE: Final = "tool.manuscript_writer.merge"
TOOL_WEB_SEARCH: Final = "tool.web_search"
INTERNAL_ROUTING: Final = "internal.routing"
INTERNAL_MEMORY: Final = "internal.memory"
INTERNAL_QUALITY_EVALUATION: Final = "internal.conversation_quality_evaluation"
INTERNAL_UNCATEGORIZED: Final = "internal.uncategorized"
CODING_AGENT_QWEN_CODE_CLI: Final = "coding_agent.qwen_code_cli"

REGISTERED_BILLING_KEYS: Final[frozenset[str]] = frozenset(
    {
        CHAT_MAIN,
        AGENT_RUN,
        DEEP_THINK_ITERATION,
        DEEP_THINK_FORCED_SYNTHESIS,
        PLAN_TASK_EXECUTION,
        PLAN_DECOMPOSITION,
        PLAN_VERIFICATION,
        TOOL_EXECUTION,
        TOOL_CODE_EXECUTOR,
        TOOL_MANUSCRIPT_WRITER,
        TOOL_MANUSCRIPT_WRITER_SECTION,
        TOOL_MANUSCRIPT_WRITER_EVALUATION,
        TOOL_MANUSCRIPT_WRITER_MERGE,
        TOOL_WEB_SEARCH,
        INTERNAL_ROUTING,
        INTERNAL_MEMORY,
        INTERNAL_QUALITY_EVALUATION,
        INTERNAL_UNCATEGORIZED,
        CODING_AGENT_QWEN_CODE_CLI,
    }
)


def normalize_billing_key(value: str | None) -> str:
    """Return a registered key or the explicit unclassified bucket."""
    candidate = str(value or "").strip().lower()
    return candidate if candidate in REGISTERED_BILLING_KEYS else INTERNAL_UNCATEGORIZED


def billing_key_for_purpose(
    call_purpose: str | None,
    tool_name: str | None = None,
) -> str:
    """Map legacy purpose/tool attribution into its stable billing key."""
    purpose = str(call_purpose or "").strip().lower()
    tool = str(tool_name or "").strip().lower()
    if purpose == "chat_main":
        return CHAT_MAIN
    if purpose == "agent_run":
        return AGENT_RUN
    if purpose == "deep_think_iteration":
        return DEEP_THINK_ITERATION
    if purpose == "deep_think_forced_synthesis":
        return DEEP_THINK_FORCED_SYNTHESIS
    if purpose == "plan_task_execution":
        return PLAN_TASK_EXECUTION
    if purpose in {"plan_decomposition", "decomposition"}:
        return PLAN_DECOMPOSITION
    if purpose in {"plan_verification", "task_verification"}:
        return PLAN_VERIFICATION
    if purpose == "conversation_quality_evaluation":
        return INTERNAL_QUALITY_EVALUATION
    if purpose in {"request_routing", "routing"}:
        return INTERNAL_ROUTING
    if purpose.startswith("memory"):
        return INTERNAL_MEMORY
    if purpose == "qwen_code_cli_execution":
        return CODING_AGENT_QWEN_CODE_CLI
    if tool == "code_executor":
        return TOOL_CODE_EXECUTOR
    if tool == "manuscript_writer":
        if "eval" in purpose:
            return TOOL_MANUSCRIPT_WRITER_EVALUATION
        if "merge" in purpose:
            return TOOL_MANUSCRIPT_WRITER_MERGE
        if "section" in purpose:
            return TOOL_MANUSCRIPT_WRITER_SECTION
        return TOOL_MANUSCRIPT_WRITER
    if tool == "web_search":
        return TOOL_WEB_SEARCH
    if purpose == "tool_execution" or tool:
        return TOOL_EXECUTION
    return INTERNAL_UNCATEGORIZED
