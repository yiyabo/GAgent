"""Regression tests for manuscript_writer liveness guards.

Production smoke observed the manuscript stage sitting silent for >900s:
the LLM call chain had no overall deadline (httpx read timeouts only bound
per-byte gaps, and SSE keep-alives reset them; final-polish clients run with
httpx timeout=None), and a failed section lost its name ('unknown'). These
tests pin the overall deadlines, heartbeat logs, and named failure rows.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import pytest

from tool_box.tools_impl import manuscript_writer as mw


# ---------------------------------------------------------------------------
# _await_with_deadline
# ---------------------------------------------------------------------------


def test_deadline_passthrough_result() -> None:
    async def quick() -> str:
        return "done"

    result = asyncio.run(
        mw._await_with_deadline(quick(), timeout_sec=5.0, heartbeat_sec=0.0, label="t")
    )
    assert result == "done"


def test_deadline_fires_on_hanging_coroutine() -> None:
    async def hang() -> str:
        await asyncio.sleep(3600)
        return "never"

    started = time.monotonic()
    with pytest.raises(asyncio.TimeoutError, match="unit-test-call.*0.2s overall deadline"):
        asyncio.run(
            mw._await_with_deadline(hang(), timeout_sec=0.2, heartbeat_sec=0.0, label="unit-test-call")
        )
    assert time.monotonic() - started < 5.0


def test_heartbeat_logs_without_deadline(caplog: pytest.LogCaptureFixture) -> None:
    async def hang() -> str:
        await asyncio.sleep(3600)
        return "never"

    async def runner() -> None:
        task = asyncio.ensure_future(
            mw._await_with_deadline(hang(), timeout_sec=None, heartbeat_sec=0.05, label="hb-label")
        )
        await asyncio.sleep(0.25)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    with caplog.at_level(logging.INFO, logger="tool_box.tools_impl.manuscript_writer"):
        asyncio.run(runner())
    assert any(
        "hb-label" in record.message and "still running" in record.message
        for record in caplog.records
    )


def test_deadline_disabled_waits_for_completion() -> None:
    async def slow() -> str:
        await asyncio.sleep(0.05)
        return "finished"

    result = asyncio.run(
        mw._await_with_deadline(slow(), timeout_sec=None, heartbeat_sec=0.0, label="t")
    )
    assert result == "finished"


def test_outer_cancellation_cancels_inner_task() -> None:
    inner_cancelled = False

    async def hang() -> str:
        nonlocal inner_cancelled
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            inner_cancelled = True
            raise
        return "never"

    async def runner() -> None:
        task = asyncio.ensure_future(
            mw._await_with_deadline(hang(), timeout_sec=30.0, heartbeat_sec=0.0, label="cancel-me")
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(runner())
    assert inner_cancelled is True


def test_env_knob_defaults_and_disable(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("MANUSCRIPT_LLM_CALL_TIMEOUT_SEC", "MANUSCRIPT_SECTION_TIMEOUT_SEC", "MANUSCRIPT_HEARTBEAT_LOG_SEC"):
        monkeypatch.delenv(key, raising=False)
    assert mw._llm_call_timeout_sec() == 600.0
    assert mw._section_timeout_sec() == 1800.0
    assert mw._heartbeat_log_sec() == 60.0

    monkeypatch.setenv("MANUSCRIPT_LLM_CALL_TIMEOUT_SEC", "0")
    monkeypatch.setenv("MANUSCRIPT_SECTION_TIMEOUT_SEC", "-1")
    monkeypatch.setenv("MANUSCRIPT_HEARTBEAT_LOG_SEC", "0")
    assert mw._llm_call_timeout_sec() is None
    assert mw._section_timeout_sec() is None
    assert mw._heartbeat_log_sec() == 0.0


# ---------------------------------------------------------------------------
# _chat overall deadline
# ---------------------------------------------------------------------------


class _HangStreamLLM:
    async def stream_chat_async(self, prompt, **kwargs):
        await asyncio.sleep(3600)
        yield "never"  # pragma: no cover

    async def chat_async(self, prompt, **kwargs):
        raise AssertionError("must not fall back after the overall deadline")


class _StreamFailHangChatLLM:
    async def stream_chat_async(self, prompt, **kwargs):
        raise RuntimeError("streaming unsupported")
        yield  # pragma: no cover

    async def chat_async(self, prompt, **kwargs):
        await asyncio.sleep(3600)
        return "never"  # pragma: no cover


def test_chat_deadline_bounds_hanging_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mw, "update_usage_context", lambda **kwargs: None)
    monkeypatch.setenv("MANUSCRIPT_LLM_CALL_TIMEOUT_SEC", "0.2")
    monkeypatch.setenv("MANUSCRIPT_HEARTBEAT_LOG_SEC", "0")
    started = time.monotonic()
    with pytest.raises(asyncio.TimeoutError, match="manuscript_writer:memo"):
        asyncio.run(mw._chat(_HangStreamLLM(), "prompt", None, purpose="manuscript_writer:memo"))
    assert time.monotonic() - started < 5.0


def test_chat_deadline_covers_fallback_chat_async(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mw, "update_usage_context", lambda **kwargs: None)
    monkeypatch.setenv("MANUSCRIPT_LLM_CALL_TIMEOUT_SEC", "0.2")
    monkeypatch.setenv("MANUSCRIPT_HEARTBEAT_LOG_SEC", "0")
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(mw._chat(_StreamFailHangChatLLM(), "prompt", None))


def test_chat_fast_stream_unaffected_by_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    class _OkLLM:
        async def stream_chat_async(self, prompt, **kwargs):
            for chunk in ("a", "b"):
                yield chunk

        async def chat_async(self, prompt, **kwargs):  # pragma: no cover
            raise AssertionError("chat_async must not run")

    monkeypatch.setattr(mw, "update_usage_context", lambda **kwargs: None)
    monkeypatch.setenv("MANUSCRIPT_LLM_CALL_TIMEOUT_SEC", "30")
    result = asyncio.run(mw._chat(_OkLLM(), "prompt", None))
    assert result == "ab"


# ---------------------------------------------------------------------------
# _build_section_failure_row
# ---------------------------------------------------------------------------


def test_section_failure_row_marks_timeout() -> None:
    row = mw._build_section_failure_row(
        section="introduction", idx=1, exc=asyncio.TimeoutError("deadline")
    )
    assert row["section"] == "introduction"
    assert row["passed"] is False
    assert row["score"] == 0.0
    assert row["defects"] == ["section_llm_timeout"]
    assert "deadline" in row["error"]


def test_section_failure_row_marks_generic_error() -> None:
    row = mw._build_section_failure_row(
        section="method", idx=3, exc=RuntimeError("boom")
    )
    assert row["defects"] == ["section_llm_error"]
    assert row["section"] == "method"


# ---------------------------------------------------------------------------
# Handler level: a hanging section fails as a named row instead of hanging
# ---------------------------------------------------------------------------


class _FakeResolver:
    def __init__(self, root: Path) -> None:
        self._root = root

    def resolve(self, **kwargs) -> Path:
        target = self._root / "mw_work"
        target.mkdir(parents=True, exist_ok=True)
        return target


def _handler_stubs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, chat_stub) -> None:
    monkeypatch.setattr(mw, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(mw, "_RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(
        mw, "_build_llm_service", lambda provider, model, **kwargs: (object(), model)
    )
    monkeypatch.setattr(mw, "_chat", chat_stub)
    monkeypatch.setattr(
        "app.services.tool_output_resolver.get_tool_output_resolver",
        lambda: _FakeResolver(tmp_path),
    )


def test_hanging_section_fails_named_section(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def _stub_chat(_llm, prompt: str, _model, **_kwargs):
        if "produce an ANALYSIS MEMO" in prompt:
            return "# Analysis Memo\n- grounded context"
        if "Evaluate the following section" in prompt:
            return json.dumps(
                {
                    "scores": {"structure": 0.95, "scientific_rigor": 0.92},
                    "defects": [],
                    "revision_instructions": [],
                    "pass": True,
                }
            )
        if "Write the section:" in prompt or "Revise the section:" in prompt:
            first_line = str(prompt).splitlines()[0].strip().lower()
            if "introduction" in first_line:
                await asyncio.sleep(3600)
                return "## Introduction\nnever"  # pragma: no cover
            return "## References\n- ref"
        return "ok"

    _handler_stubs(monkeypatch, tmp_path, _stub_chat)
    monkeypatch.setenv("MANUSCRIPT_SECTION_TIMEOUT_SEC", "0.3")
    monkeypatch.setenv("MANUSCRIPT_HEARTBEAT_LOG_SEC", "0")

    started = time.monotonic()
    result = asyncio.run(
        mw.manuscript_writer_handler(
            task="Write a manuscript.",
            output_path="runtime/test_mw_sandbox/out.md",
            sections=["introduction", "references"],
            keep_workspace=True,
        )
    )
    elapsed = time.monotonic() - started

    assert elapsed < 30.0, "handler must not hang on a stalled section"
    assert result["success"] is False
    assert result["error"] == "section_evaluation_failed"
    assert result["failed_sections"] == ["introduction"]
    intro_rows = [row for row in result["sections"] if row.get("section") == "introduction"]
    assert intro_rows and intro_rows[0]["defects"] == ["section_llm_timeout"]
    assert "unknown" not in result["failed_sections"]


def test_fast_sections_still_pass_with_guards_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def _stub_chat(_llm, prompt: str, _model, **_kwargs):
        if "produce an ANALYSIS MEMO" in prompt:
            return "# Analysis Memo\n- grounded context"
        if "Evaluate the following section" in prompt:
            return json.dumps(
                {
                    "scores": {"structure": 0.95, "scientific_rigor": 0.92},
                    "defects": [],
                    "revision_instructions": [],
                    "pass": True,
                }
            )
        if "Write the section:" in prompt or "Revise the section:" in prompt:
            return "## Section\nDraft text."
        if "final manuscript editor" in prompt:
            return "## Final Manuscript\nPolished."
        if "final release gate reviewer" in prompt:
            return json.dumps(
                {
                    "scores": {"polish_quality": 0.95, "readability": 0.95},
                    "defects": [],
                    "revision_instructions": [],
                    "pass": True,
                    "release_summary": "ok",
                }
            )
        if "scientific writing editor" in prompt.lower():
            return "unchanged"
        return "ok"

    _handler_stubs(monkeypatch, tmp_path, _stub_chat)
    monkeypatch.setenv("MANUSCRIPT_SECTION_TIMEOUT_SEC", "30")
    monkeypatch.setenv("MANUSCRIPT_LLM_CALL_TIMEOUT_SEC", "30")
    monkeypatch.setenv("MANUSCRIPT_FINAL_POLISH_ENABLED", "false")

    result = asyncio.run(
        mw.manuscript_writer_handler(
            task="Write a manuscript.",
            output_path="runtime/test_mw_sandbox_ok/out.md",
            sections=["introduction", "references"],
            keep_workspace=False,
        )
    )
    assert result["success"] is True
    assert result["failed_sections"] == []
    assert (tmp_path / "runtime" / "test_mw_sandbox_ok" / "out.md").exists()
