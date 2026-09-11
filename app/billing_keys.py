"""Billing keys for LLM-bearing Agent capabilities.

A billing key identifies the product capability that caused an upstream LLM
request; it is the value sent in the ``X-Agent-Tool-Key`` header and stored
in ``llm_usage_log.billing_key``.  It is intentionally independent from
provider, model, and API key.

Registry (17 keys, aligned with the 2026-09-10 统计口径清单):
  chat.main / chat.routing
  deep_think.iteration / deep_think.forced_synthesis
  plan.decompose / plan.task_execution / plan.review / plan.optimize
  tool.execution / tool.code_executor / tool.web_search
  coding_agent.qwen_code_cli
  internal.conversation_quality_evaluation / internal.memory_embedding
  internal.routing / internal.uncategorized
"""
from __future__ import annotations

from typing import Final

CHAT_MAIN: Final = "chat.main"
CHAT_ROUTING: Final = "chat.routing"
DEEP_THINK_ITERATION: Final = "deep_think.iteration"
DEEP_THINK_FORCED_SYNTHESIS: Final = "deep_think.forced_synthesis"
PLAN_DECOMPOSE: Final = "plan.decompose"
PLAN_TASK_EXECUTION: Final = "plan.task_execution"
PLAN_REVIEW: Final = "plan.review"
PLAN_OPTIMIZE: Final = "plan.optimize"
TOOL_EXECUTION: Final = "tool.execution"
TOOL_CODE_EXECUTOR: Final = "tool.code_executor"
TOOL_WEB_SEARCH: Final = "tool.web_search"
TOOL_LITERATURE_PIPELINE: Final = "tool.literature_pipeline"
TOOL_VISION_READER: Final = "tool.vision_reader"
CODING_AGENT_QWEN_CODE_CLI: Final = "coding_agent.qwen_code_cli"
INTERNAL_CONVERSATION_QUALITY_EVALUATION: Final = "internal.conversation_quality_evaluation"
INTERNAL_MEMORY_EMBEDDING: Final = "internal.memory_embedding"
INTERNAL_ROUTING: Final = "internal.routing"
INTERNAL_UNCATEGORIZED: Final = "internal.uncategorized"

# Toll-free keys recorded in the ledger but excluded from user billing
# until pricing says otherwise.
INTERNAL_KEYS: Final[frozenset[str]] = frozenset(
    {
        INTERNAL_CONVERSATION_QUALITY_EVALUATION,
        INTERNAL_MEMORY_EMBEDDING,
        INTERNAL_ROUTING,
        INTERNAL_UNCATEGORIZED,
    }
)

REGISTERED_BILLING_KEYS: Final[frozenset[str]] = frozenset(
    {
        CHAT_MAIN,
        CHAT_ROUTING,
        DEEP_THINK_ITERATION,
        DEEP_THINK_FORCED_SYNTHESIS,
        PLAN_DECOMPOSE,
        PLAN_TASK_EXECUTION,
        PLAN_REVIEW,
        PLAN_OPTIMIZE,
        TOOL_EXECUTION,
        TOOL_CODE_EXECUTOR,
        TOOL_WEB_SEARCH,
        TOOL_LITERATURE_PIPELINE,
        TOOL_VISION_READER,
        CODING_AGENT_QWEN_CODE_CLI,
        INTERNAL_CONVERSATION_QUALITY_EVALUATION,
        INTERNAL_MEMORY_EMBEDDING,
        INTERNAL_ROUTING,
        INTERNAL_UNCATEGORIZED,
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
    """Map legacy purpose/tool attribution into its stable billing key.

    Unknown combinations must land in ``internal.uncategorized`` — never a
    silent empty value (口径清单: 归因不了的显式落此，不许静默空值).
    """
    purpose = str(call_purpose or "").strip().lower()
    tool = str(tool_name or "").strip().lower()

    # Chat-side purposes (tool_name wins over chat purposes: a tool-scoped
    # update inside a chat run reroutes attribution to the tool)
    if purpose in {"request_routing", "routing", "intent_classification"}:
        return CHAT_ROUTING
    # Deep think (legacy purpose strings also map)
    if purpose in {"deep_think_iteration", "deep_think", "deep_think_agent"}:
        return DEEP_THINK_ITERATION
    if purpose in {"deep_think_forced_synthesis", "forced_synthesis"}:
        return DEEP_THINK_FORCED_SYNTHESIS
    # Plan chain
    if purpose in {"plan_decomposition", "decomposition", "plan_decompose"}:
        return PLAN_DECOMPOSE
    if purpose == "plan_task_execution":
        return PLAN_TASK_EXECUTION
    if purpose in {"plan_review", "plan_verification", "task_verification"}:
        return PLAN_REVIEW
    if purpose in {"plan_optimize", "plan_optimization"}:
        return PLAN_OPTIMIZE
    # Coding agent (subprocess of its own)
    if purpose == "qwen_code_cli_execution":
        return CODING_AGENT_QWEN_CODE_CLI
    # Tool-internal LLM calls inherit the tool key (checked before chat
    # purposes so tool-scoped context updates reroute attribution)
    if tool == "code_executor":
        return TOOL_CODE_EXECUTOR
    if tool == "web_search":
        return TOOL_WEB_SEARCH
    if tool == "literature_pipeline" or purpose == "literature_pipeline":
        return TOOL_LITERATURE_PIPELINE
    if tool == "vision_reader" or purpose == "vision_reader":
        return TOOL_VISION_READER
    if purpose in {"chat_main", "simple_chat", "chat"}:
        return CHAT_MAIN
    # Internal self-consumption
    if purpose == "conversation_quality_evaluation":
        return INTERNAL_CONVERSATION_QUALITY_EVALUATION
    if purpose.startswith("memory") or purpose.endswith("embedding"):
        return INTERNAL_MEMORY_EMBEDDING
    if purpose in {"internal", "fallback"}:
        return INTERNAL_ROUTING
    # Generic tool execution carries any other tool's internal LLM calls
    if purpose == "tool_execution" or tool:
        return TOOL_EXECUTION
    return INTERNAL_UNCATEGORIZED
