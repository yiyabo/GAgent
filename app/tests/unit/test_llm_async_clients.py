from __future__ import annotations

import asyncio
from collections.abc import Generator
from typing import Any

import pytest

import app.llm as llm_module
from app.llm import LLMClient


class _LoopBoundResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"choices": [{"message": {"content": "ok"}}]}


class _LoopBoundAsyncClient:
    instances: list["_LoopBoundAsyncClient"] = []

    def __init__(self, **_: object) -> None:
        self.loop = asyncio.get_running_loop()
        self.closed = False
        self.post_calls = 0
        self.instances.append(self)

    @property
    def is_closed(self) -> bool:
        return self.closed

    async def post(self, *_: object, **__: object) -> _LoopBoundResponse:
        assert asyncio.get_running_loop() is self.loop
        assert not self.closed
        self.post_calls += 1
        return _LoopBoundResponse()

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _clean_llm_clients() -> Generator[None, None, None]:
    asyncio.run(llm_module.close_shared_clients())
    _LoopBoundAsyncClient.instances.clear()
    yield
    asyncio.run(llm_module.close_shared_clients())
    _LoopBoundAsyncClient.instances.clear()


def test_async_client_is_reused_within_one_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_module.httpx, "AsyncClient", _LoopBoundAsyncClient)

    async def _exercise() -> None:
        first = llm_module._get_shared_async_client()
        second = llm_module._get_shared_async_client()
        assert first is second
        await llm_module.close_current_loop_async_client()
        assert first.is_closed

    asyncio.run(_exercise())
    assert len(_LoopBoundAsyncClient.instances) == 1


def test_chat_async_uses_event_loop_scoped_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_module.httpx, "AsyncClient", _LoopBoundAsyncClient)
    client = LLMClient(
        provider="qwen",
        api_key="test-key",
        url="https://example.test/v1/chat/completions",
        model="qwen-test",
        retries=0,
    )

    async def _call() -> str:
        return await client.chat_async("hello")

    assert asyncio.run(_call()) == "ok"
    assert asyncio.run(_call()) == "ok"

    assert len(_LoopBoundAsyncClient.instances) == 2
    assert _LoopBoundAsyncClient.instances[0] is not _LoopBoundAsyncClient.instances[1]
    assert _LoopBoundAsyncClient.instances[0].loop is not _LoopBoundAsyncClient.instances[1].loop
    assert [instance.post_calls for instance in _LoopBoundAsyncClient.instances] == [1, 1]
