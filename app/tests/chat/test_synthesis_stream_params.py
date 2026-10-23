"""The closing summary sends its token budget and the thinking switch explicitly.

Production 2026-10-09 (LOCAL_INFRA §109): forced synthesis asked for 6000
tokens but every call went out at LLM_MAX_TOKENS=4096 (the kwarg was swallowed
by `stream_chat_async`'s `**_`) and without `enable_thinking`, so the provider
default reasoned through the whole budget: 0 visible chars, three identical
retries, then a canned fallback reached the user. The closing summary keeps
both parameters explicit, uses the loop's own tool-calling transport with tool
calling switched off, and retries once with thinking off.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app.llm import NativeStreamResult
from app.services.deep_think import synthesis
from app.services.deep_think.models import ThinkingStep


class _NativeRecordingLLM:
    def __init__(self, *contents: str) -> None:
        self.calls: List[Dict[str, Any]] = []
        self._contents = list(contents) or ["synthesized answer"]

    async def stream_chat_with_tools_async(self, **kwargs: Any) -> NativeStreamResult:
        self.calls.append(kwargs)
        content = self._contents[min(len(self.calls), len(self._contents)) - 1]
        return NativeStreamResult(content=content, finish_reason="stop")


class _StreamOnlyLLM:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    async def stream_chat_async(self, prompt: str, **kwargs: Any):
        self.calls.append({"prompt": prompt, **kwargs})
        yield "synthesized "
        yield "answer"


def _agent(llm: Any) -> SimpleNamespace:
    return SimpleNamespace(llm_client=llm, cancel_event=None)


@pytest.mark.asyncio()
async def test_native_transport_forwards_messages_budget_tools_and_switches_tool_calling_off() -> None:
    llm = _NativeRecordingLLM()
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}]
    tools = [{"type": "function", "function": {"name": "web_search"}}]

    text = await synthesis._chat_messages_streaming(
        _agent(llm), messages, max_tokens=16384, enable_thinking=False, thinking_budget=None, tools=tools
    )

    assert text == "synthesized answer"
    call = llm.calls[0]
    assert call["messages"] is messages
    assert call["tools"] == tools
    assert call["tool_choice"] == "none"
    assert call["max_tokens"] == 16384
    assert call["enable_thinking"] is False


@pytest.mark.asyncio()
async def test_stream_only_client_gets_the_full_messages_and_explicit_params() -> None:
    llm = _StreamOnlyLLM()
    messages = [{"role": "user", "content": "q"}]

    text = await synthesis._chat_messages_streaming(
        _agent(llm), messages, max_tokens=1200, enable_thinking=True
    )

    assert text == "synthesized answer"
    assert llm.calls[0]["messages"] == messages
    assert llm.calls[0]["max_tokens"] == 1200
    assert llm.calls[0]["enable_thinking"] is True


def _steps() -> List[ThinkingStep]:
    return [ThinkingStep(iteration=1, thought="", action=None, action_result=None, self_correction=None)]


def _real_agent(llm: Any):
    from app.services.deep_think_agent import DeepThinkAgent

    return DeepThinkAgent(llm_client=llm, available_tools=[], tool_executor=lambda *_: None,
                          request_profile={"intent_type": "chat"})


@pytest.mark.asyncio()
async def test_closing_summary_defaults_to_the_16384_token_cap_and_honours_the_thinking_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(synthesis, "get_settings", lambda: SimpleNamespace(thinking_enabled=False))
    monkeypatch.delenv("DEEP_THINK_SYNTHESIS_MAX_TOKENS", raising=False)
    answer = "Here is the complete closing summary of everything that happened in this run."
    llm = _NativeRecordingLLM(answer)

    result = await synthesis._forced_synthesis_from_steps(
        _real_agent(llm), _steps(), "Explain what happened", [{"role": "user", "content": "Explain what happened"}]
    )

    assert result == answer
    assert len(llm.calls) == 1
    assert llm.calls[0]["max_tokens"] == 16384
    assert llm.calls[0]["enable_thinking"] is False


@pytest.mark.asyncio()
async def test_an_empty_first_reply_is_retried_once_with_thinking_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A thinking model that spends the whole cap reasoning returns nothing visible."""
    monkeypatch.setattr(synthesis, "get_settings", lambda: SimpleNamespace(thinking_enabled=True))
    answer = "Here is the complete closing summary of everything that happened in this run."
    llm = _NativeRecordingLLM("", answer)
    agent = _real_agent(llm)
    agent.thinking_budget = 4000

    result = await synthesis._forced_synthesis_from_steps(
        agent, _steps(), "Explain what happened", [{"role": "user", "content": "Explain what happened"}]
    )

    assert result == answer
    assert [c["enable_thinking"] for c in llm.calls] == [True, False]
    assert llm.calls[0]["thinking_budget"] == 4000
    assert llm.calls[1]["thinking_budget"] is None


@pytest.mark.asyncio()
async def test_two_empty_replies_return_nothing_so_the_fixed_fallback_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(synthesis, "get_settings", lambda: SimpleNamespace(thinking_enabled=True))
    llm = _NativeRecordingLLM("")

    result = await synthesis._forced_synthesis_from_steps(
        _real_agent(llm), _steps(), "Explain what happened", [{"role": "user", "content": "Explain what happened"}]
    )

    assert result == ""
    assert len(llm.calls) == 2
