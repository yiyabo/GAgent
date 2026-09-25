"""Execution-LLM wrapper (god-class split, behaviour zero-change).

``PlanExecutorLLMService`` was moved verbatim out of ``plan_executor.py`` per
``design/2026-09-24-backend-godfiles-refactor-plan.md`` §4.6 (cluster ③).
The facade re-exports the class, so ``routers/chat/agent.py`` (two direct
instantiation sites) and the tests import it unchanged.

Deviation from byte-verbatim bodies: none.  ``_strip_code_fences`` and
``_run_coroutine_sync`` are imported from their new sibling modules instead of
being module globals of the facade; both were verified unpatched (no test
patches either name in any namespace), so the binding is equivalent.  The
module has its own ``logging.getLogger(__name__)`` (split precedent).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, cast

from pydantic import ValidationError

from ...config.executor_config import ExecutorSettings, get_executor_settings
from ...llm import LLMClient, NativeStreamResult
from ..llm.llm_service import LLMService
from .executor_models import ExecutionConfig, ExecutionResponse, ToolCallRequest
from .executor_prompts import _strip_code_fences
from .executor_text_utils import _run_coroutine_sync

logger = logging.getLogger(__name__)


class PlanExecutorLLMService:
    """Wrapper around LLMService dedicated to execution prompts."""

    def __init__(
        self,
        *,
        llm: Optional[LLMService] = None,
        settings: Optional[ExecutorSettings] = None,
    ) -> None:
        self._settings = settings or get_executor_settings()
        if llm is not None:
            self._llm = llm
        else:
            client: Optional[LLMClient] = None
            if any((self._settings.provider, self._settings.api_url, self._settings.api_key)):
                client = LLMClient(
                    provider=self._settings.provider,
                    api_key=self._settings.api_key,
                    url=self._settings.api_url,
                    model=self._settings.model,
                )
            self._llm = LLMService(client)
        # Store direct client reference for native tool calling
        # (LLMService.client is always set — either the passed-in client or
        # the default one created by get_default_client()).
        self._llm_client: LLMClient = cast(LLMClient, self._llm.client)

    def generate(
        self,
        prompt: str,
        config: ExecutionConfig,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> ExecutionResponse:
        kwargs: Dict[str, Any] = {}
        model = config.model or self._settings.model
        if model:
            kwargs["model"] = model

        if tools is not None:
            return self._generate_with_tools(prompt, tools, **kwargs)

        # Legacy fallback (no tools)
        if config.timeout is not None:
            kwargs["timeout"] = config.timeout
        response_text = self._llm.chat(prompt, **kwargs)
        cleaned = _strip_code_fences(response_text)
        try:
            return ExecutionResponse.model_validate_json(cleaned)
        except ValidationError:
            logger.error("Failed to parse execution response: %s", cleaned)
            raise

    # ------------------------------------------------------------------
    # Status inference for text-only responses in native tool-calling mode
    # ------------------------------------------------------------------

    _FAILURE_TOKENS = (
        "traceback",
        "exception",
        "failed",
        "error:",
        "unable to",
        "timed out",
        "cannot complete",
        "cannot be completed",
        "not possible",
        "无法完成",
        "执行失败",
        "出错",
        "异常",
    )

    _BLOCKED_TOKENS = (
        "blocked by dependencies",
        "dependency outputs are missing",
        "incomplete dependencies",
        "unmet dependencies",
        "prerequisite",
        "requires output from",
        "waiting for",
        "被依赖阻断",
        "依赖未完成",
        "前置任务",
    )

    _SKIPPED_TOKENS = (
        "skipped",
        "not applicable",
        "out of scope",
        "already completed",
        "已跳过",
        "不适用",
        "已完成",
    )

    @classmethod
    def _infer_text_response_status(cls, content: str) -> str:
        """Infer execution status from a text-only LLM response.

        When the native tool-calling path receives no tool_calls, the LLM
        replied with prose.  We apply heuristic detection to avoid mapping
        refusals, blockers, and failures to 'success'.
        """
        if not content or not content.strip():
            return "success"

        lowered = content.strip().lower()

        # Check blocked/dependency signals first (more specific)
        if any(token in lowered for token in cls._BLOCKED_TOKENS):
            return "skipped"

        # Check failure signals
        if any(token in lowered for token in cls._FAILURE_TOKENS):
            return "failed"

        # Check skipped signals
        if any(token in lowered for token in cls._SKIPPED_TOKENS):
            # "already completed" is ambiguous — could mean success
            if "already completed" in lowered or "已完成" in lowered:
                return "success"
            return "skipped"

        return "success"

    def _generate_with_tools(
        self,
        prompt: str,
        tools: List[Dict[str, Any]],
        **kwargs: Any,
    ) -> ExecutionResponse:
        """Call LLM with native tool calling, synchronously."""
        # Split prompt into system + user messages.
        # The first line is the SYSTEM_HEADER from ExecutorPromptBuilder.
        system_msg = prompt.split("\n", 1)[0]
        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": prompt},
        ]

        async def _call() -> NativeStreamResult:
            return await self._llm_client.stream_chat_with_tools_async(
                messages=messages,
                tools=tools,
                tool_choice="auto",
                **kwargs,
            )

        # Bridge async → sync using the module-level helper.
        result: NativeStreamResult = _run_coroutine_sync(_call())

        # Convert NativeStreamResult → ExecutionResponse
        if result.tool_calls:
            first_tc = result.tool_calls[0]
            return ExecutionResponse(
                status="needs_tool",
                content=result.content or "",
                tool_call=ToolCallRequest(
                    name=first_tc.name,
                    parameters=first_tc.arguments,
                ),
            )
        else:
            content = result.content or ""
            status = self._infer_text_response_status(content)
            return ExecutionResponse(
                status=status,
                content=content,
            )
