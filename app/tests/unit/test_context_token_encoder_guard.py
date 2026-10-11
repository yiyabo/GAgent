"""The first tiktoken load must be bounded and must not freeze the event loop.

2026-10-12 incident: the app image ships tiktoken, ``estimate_tokens`` lazily
loads the BPE file on first use, and ``tiktoken.get_encoding`` fetches it over
the network when the cache is cold.  On .8 the blob endpoint is unreachable,
so the first chat message blocked the event loop for ~390 s; the run's lease
expired mid-freeze and died with StaleRunClaim.  The image now bakes the
cache (deploy/tiktoken-cache + TIKTOKEN_CACHE_DIR), and ``_get_encoder``
bounds the load and remembers a failure as the offline fallback.
"""

from __future__ import annotations

import time

from app.services.context import context_manager as cm


def test_first_load_is_bounded_and_failure_is_cached(monkeypatch):
    calls = {"n": 0}

    def hanging_get_encoding(name: str):
        calls["n"] += 1
        time.sleep(30)  # stands in for the unreachable-blob fetch
        return None

    monkeypatch.setattr(cm, "_ENCODER", None)
    monkeypatch.setattr(cm, "_ENCODER_UNAVAILABLE", False)
    monkeypatch.setattr(cm, "_TIKTOKEN_LOAD_TIMEOUT_S", 0.5)
    monkeypatch.setattr("tiktoken.get_encoding", hanging_get_encoding)

    started = time.monotonic()
    tokens = cm.estimate_tokens("hello world")
    elapsed = time.monotonic() - started

    assert elapsed < 5.0, f"estimate_tokens blocked for {elapsed:.1f}s"
    assert tokens >= 1
    assert cm._ENCODER_UNAVAILABLE is True

    # The failure is remembered: the next call must not retry the load.
    started = time.monotonic()
    cm.estimate_tokens("hello again")
    assert time.monotonic() - started < 0.5
    assert calls["n"] == 1


def test_cached_encoder_still_used_when_available(monkeypatch):
    class _Enc:
        def encode(self, text: str):
            return list(range(7))

    monkeypatch.setattr(cm, "_ENCODER", _Enc())
    monkeypatch.setattr(cm, "_ENCODER_UNAVAILABLE", False)
    assert cm.estimate_tokens("whatever") == 7
