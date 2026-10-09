"""Synthesis streaming sends its token budget and the thinking switch explicitly.

Production 2026-10-09 (LOCAL_INFRA §109): forced synthesis asked for 6000
tokens but every call went out at LLM_MAX_TOKENS=4096 (the kwarg was swallowed
by `stream_chat_async`'s `**_`) and without `enable_thinking`, so the provider
default reasoned through the whole budget — 0 visible chars, three identical
retries, then the structured fallback template reached the user.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app.services.deep_think import synthesis


class _RecordingLLM:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    async def stream_chat_async(self, prompt: str, **kwargs: Any):
        self.calls.append({"prompt": prompt, **kwargs})
        yield "synthesized "
        yield "answer"


def _agent(llm: _RecordingLLM) -> SimpleNamespace:
    return SimpleNamespace(llm_client=llm, cancel_event=None)


@pytest.mark.asyncio()
async def test_streaming_synthesis_forwards_budget_and_disables_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(synthesis, "get_settings", lambda: SimpleNamespace(thinking_enabled=False))
    llm = _RecordingLLM()

    text = await synthesis._chat_text_streaming(
        _agent(llm), "Please provide your complete answer now:", max_tokens=6000
    )

    assert text == "synthesized answer"
    assert llm.calls[0]["max_tokens"] == 6000
    assert llm.calls[0]["enable_thinking"] is False


@pytest.mark.asyncio()
async def test_streaming_synthesis_honours_thinking_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(synthesis, "get_settings", lambda: SimpleNamespace(thinking_enabled=True))
    llm = _RecordingLLM()

    await synthesis._chat_text_streaming(_agent(llm), "prompt", max_tokens=1200)

    assert llm.calls[0]["max_tokens"] == 1200
    assert llm.calls[0]["enable_thinking"] is True
