"""Cross-turn verified execution ledger (Phase 0).

Regression guard for the 2026-11-13 live audit: a follow-up turn claimed "no
tool was ever executed" although the run database held 23 real tool calls.
``apply_execution_ledger`` must rebuild that evidence from chat_runs/run_steps
and attach it to the trailing assistant history messages, fail open on any
database error, and stay silent for runs without tool steps.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.routers.chat import execution_ledger as ledger_mod


def _step(tool: str, status: str = "succeeded", refs: tuple = ()) -> SimpleNamespace:
    return SimpleNamespace(tool_name=tool, status=status, output_refs=refs)


def _history() -> list[dict[str, Any]]:
    return [
        {"role": "user", "content": "筛选噬菌体"},
        {"role": "assistant", "content": "已完成筛选。"},
        {"role": "user", "content": "你刚才真的执行了吗"},
    ]


@pytest.fixture(autouse=True)
def _enable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAT_EXECUTION_LEDGER_ENABLED", "1")


def _install(
    monkeypatch: pytest.MonkeyPatch,
    runs: list[dict[str, Any]],
    steps_by_run: dict[str, list],
) -> None:
    monkeypatch.setattr(
        "app.repository.chat_runs.list_session_runs",
        lambda session_id, limit=10, **_: runs[:limit],
    )
    monkeypatch.setattr(
        "app.repository.run_steps.list_steps",
        lambda run_id: steps_by_run.get(run_id, []),
    )


class TestBuildRunLedger:
    def test_counts_tools_and_failures_and_artifacts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install(monkeypatch, [], {})
        run = {"run_id": "dt_abc", "status": "succeeded"}
        steps = [
            _step("phagescope_data"),
            _step("phagescope_data"),
            _step("phagescope_data", status="failed"),
            _step("bio_tools", refs=("results/phagescope/x/genomes.fasta",)),
        ]
        monkeypatch.setattr("app.repository.run_steps.list_steps", lambda run_id: steps)
        text = ledger_mod.build_run_ledger(run)
        assert text is not None
        assert "run=dt_abc" in text
        assert "4 tool call(s)" in text
        assert "phagescope_data×3" in text
        assert "bio_tools×1" in text
        assert "3 succeeded, 1 not-succeeded" in text
        assert "results/phagescope/x/genomes.fasta" in text
        assert "system-recorded facts" in text

    def test_run_without_steps_produces_no_ledger(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install(monkeypatch, [], {})
        assert ledger_mod.build_run_ledger({"run_id": "dt_empty", "status": "succeeded"}) is None


class TestApplyExecutionLedger:
    def test_attaches_to_last_assistant_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        runs = [{"run_id": "dt_1", "status": "succeeded"}]
        steps = {"dt_1": [_step("phagescope_data"), _step("bio_tools")]}
        _install(monkeypatch, runs, steps)
        history = _history()
        updated = ledger_mod.apply_execution_ledger(history, "session_x")
        assert updated is not history
        assert "[Verified Execution Ledger run=dt_1" in updated[1]["content"]
        assert updated[1]["content"].startswith("已完成筛选。")
        # User messages are untouched; original history object is not mutated.
        assert updated[0]["content"] == "筛选噬菌体"
        assert "[Verified Execution Ledger" not in history[1]["content"]

    def test_multiple_runs_map_to_assistant_messages_in_recency_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runs = [
            {"run_id": "dt_new", "status": "succeeded"},
            {"run_id": "dt_old", "status": "failed"},
        ]
        steps = {"dt_new": [_step("bio_tools")], "dt_old": [_step("phagescope_data")]}
        _install(monkeypatch, runs, steps)
        history = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
        ]
        updated = ledger_mod.apply_execution_ledger(history, "session_x")
        assert "run=dt_new" in updated[3]["content"]
        assert "run=dt_old" in updated[1]["content"]

    def test_running_runs_are_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        runs = [
            {"run_id": "dt_running", "status": "running"},
            {"run_id": "dt_done", "status": "succeeded"},
        ]
        steps = {"dt_done": [_step("bio_tools")], "dt_running": [_step("bio_tools")]}
        _install(monkeypatch, runs, steps)
        updated = ledger_mod.apply_execution_ledger(_history(), "session_x")
        assert "run=dt_done" in updated[1]["content"]
        assert "dt_running" not in updated[1]["content"]

    def test_no_tool_runs_leaves_history_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install(monkeypatch, [{"run_id": "dt_1", "status": "succeeded"}], {})
        history = _history()
        assert ledger_mod.apply_execution_ledger(history, "session_x") is history

    def test_database_error_fails_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(session_id: str, limit: int = 10, **_: Any) -> list:
            raise RuntimeError("plan_registry unavailable")

        monkeypatch.setattr("app.repository.chat_runs.list_session_runs", _boom)
        history = _history()
        assert ledger_mod.apply_execution_ledger(history, "session_x") is history

    def test_disabled_by_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CHAT_EXECUTION_LEDGER_ENABLED", "0")
        _install(monkeypatch, [{"run_id": "dt_1", "status": "succeeded"}], {"dt_1": [_step("bio_tools")]})
        history = _history()
        assert ledger_mod.apply_execution_ledger(history, "session_x") is history

    def test_empty_session_or_history_short_circuits(self) -> None:
        history = _history()
        assert ledger_mod.apply_execution_ledger(history, None) is history
        assert ledger_mod.apply_execution_ledger([], "session_x") == []

    def test_ledger_text_is_bounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        steps = [_step(f"tool_with_a_rather_long_name_{i}") for i in range(30)]
        _install(monkeypatch, [], {})
        monkeypatch.setattr("app.repository.run_steps.list_steps", lambda run_id: steps)
        text = ledger_mod.build_run_ledger({"run_id": "dt_big", "status": "succeeded"})
        assert text is not None
        assert len(text) <= ledger_mod.MAX_LEDGER_CHARS_PER_RUN


class TestPromptRule:
    def test_deep_think_prompt_declares_ledger_truth(self) -> None:
        from pathlib import Path

        prompts = Path(__file__).resolve().parents[2] / "services" / "deep_think" / "prompts.py"
        text = prompts.read_text(encoding="utf-8")
        assert "VERIFIED EXECUTION LEDGER" in text
        assert "never deny that those" in text
