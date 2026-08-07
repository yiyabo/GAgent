#!/usr/bin/env python3
"""LLM health endpoint: ping results must be cached within the TTL."""

import app.main as main_mod


class _FakeLLMClient:
    def __init__(self) -> None:
        self.ping_calls = 0

    def config(self):
        return {"provider": "fake", "model": "fake-1"}

    def ping(self):
        self.ping_calls += 1
        return True


def _reset_ping_cache() -> None:
    main_mod._llm_ping_cache["checked"] = False
    main_mod._llm_ping_cache["value"] = None
    main_mod._llm_ping_cache["expires_at"] = 0.0


def test_llm_health_ping_cached_within_ttl(monkeypatch) -> None:
    fake = _FakeLLMClient()
    monkeypatch.setattr(main_mod, "get_default_client", lambda: fake)
    _reset_ping_cache()

    first = main_mod.llm_health(ping=True)
    second = main_mod.llm_health(ping=True)

    assert fake.ping_calls == 1
    assert first["ping_ok"] is True
    assert first["ping_cached"] is False
    assert second["ping_ok"] is True
    assert second["ping_cached"] is True


def test_llm_health_ping_refreshes_after_ttl(monkeypatch) -> None:
    fake = _FakeLLMClient()
    monkeypatch.setattr(main_mod, "get_default_client", lambda: fake)
    _reset_ping_cache()

    main_mod.llm_health(ping=True)
    main_mod._llm_ping_cache["expires_at"] = 0.0  # force expiry
    main_mod.llm_health(ping=True)

    assert fake.ping_calls == 2


def test_llm_health_without_ping_never_calls_upstream(monkeypatch) -> None:
    fake = _FakeLLMClient()
    monkeypatch.setattr(main_mod, "get_default_client", lambda: fake)
    _reset_ping_cache()

    info = main_mod.llm_health(ping=False)

    assert info["ping_ok"] is None
    assert fake.ping_calls == 0
