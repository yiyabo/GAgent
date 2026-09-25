"""Parent/child linkage for sub-agent delegation rows in ``llm_usage_log``.

The delegated run (``code_executor`` → qwen_code CLI) records its own ``run_id``
and links back to the run that delegated it (``parent_run_id``), read from the
ambient LLM usage context.  Without a visible parent context — or when the
ambient run is the child's own run — the column stays NULL so pre-existing
accounting rows keep their exact meaning.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.repository.llm_usage import init_llm_usage_table

_CHILD_RUN_ID = "20260925_120000_000000_deadbeef"


@pytest.fixture
def ledger_db(tmp_path: Path):
    """Point the shared SQLite pool at a throwaway ledger instead of the repo DB."""
    from app.database_pool import close_connection_pool, initialize_connection_pool

    initialize_connection_pool(db_path=str(tmp_path / "llm_usage_ledger.db"))
    try:
        yield tmp_path / "llm_usage_ledger.db"
    finally:
        close_connection_pool()


@pytest.fixture(autouse=True)
def _reset_usage_context():
    from app.llm import _usage_context

    _usage_context.set(None)
    yield
    _usage_context.set(None)


def _record(*, run_id: str = _CHILD_RUN_ID, call_status: str = "ok", parent_run_id: str | None = None):
    from tool_box.tools_impl import code_executor as code_executor_module

    return code_executor_module._record_external_cli_usage(
        provider="qwen_code_cli",
        model="qwen3.7-max",
        prompt_tokens=1000,
        completion_tokens=200,
        session_id="session-x",
        plan_id=None,
        task_id=None,
        call_purpose="qwen_code_cli_execution",
        duration_ms=1234.5,
        run_id=run_id,
        tool_name="code_executor",
        call_status=call_status,
        parent_run_id=parent_run_id,
    )


def _usage_rows(session_id: str = "session-x") -> list[dict]:
    from app.database_pool import get_db

    with get_db() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM llm_usage_log WHERE session_id = ?", (session_id,)
            )
        ]


def test_delegation_row_links_parent_from_usage_context(ledger_db: Path) -> None:
    from app.llm import clear_usage_context, set_usage_context

    init_llm_usage_table()
    token = set_usage_context(
        session_id="session-x",
        call_purpose="online_execution",
        phase="execution",
        run_id="chat_run_42",
    )
    try:
        usage = _record()
    finally:
        clear_usage_context(token)

    assert usage is not None
    rows = _usage_rows()
    assert len(rows) == 1
    row = rows[0]
    # Child identity is unchanged; the parent is an added link.
    assert row["run_id"] == _CHILD_RUN_ID
    assert row["parent_run_id"] == "chat_run_42"
    assert row["tool_name"] == "code_executor"
    assert row["call_status"] == "ok"
    assert row["duration_ms"] == 1234.5


def test_delegation_row_has_no_parent_without_usage_context(ledger_db: Path) -> None:
    init_llm_usage_table()

    usage = _record(call_status="error")

    assert usage is not None
    rows = _usage_rows()
    assert len(rows) == 1
    assert rows[0]["run_id"] == _CHILD_RUN_ID
    assert rows[0]["parent_run_id"] is None
    assert rows[0]["call_status"] == "error"


def test_delegation_row_skips_self_referential_parent(ledger_db: Path) -> None:
    from app.llm import clear_usage_context, set_usage_context

    init_llm_usage_table()
    token = set_usage_context(session_id="session-x", run_id=_CHILD_RUN_ID)
    try:
        _record()
    finally:
        clear_usage_context(token)

    rows = _usage_rows()
    assert len(rows) == 1
    assert rows[0]["parent_run_id"] is None


def test_explicit_parent_run_id_wins_over_usage_context(ledger_db: Path) -> None:
    from app.llm import clear_usage_context, set_usage_context

    init_llm_usage_table()
    token = set_usage_context(session_id="session-x", run_id="ambient_run")
    try:
        _record(parent_run_id="declared_parent")
    finally:
        clear_usage_context(token)

    rows = _usage_rows()
    assert len(rows) == 1
    assert rows[0]["parent_run_id"] == "declared_parent"


async def test_delegation_accounting_sees_parent_run_across_thread_hop(
    monkeypatch: pytest.MonkeyPatch,
    ledger_db: Path,
) -> None:
    """The plan/chat delegation hop must not lose the ambient run attribution.

    Mirrors production: a worker thread runs ``UnifiedToolExecutor.execute_sync``
    (``asyncio.run`` inside the thread), which awaits the tool handler, which
    books the delegation.  The parent context set by the caller has to survive
    the hops.
    """
    import tool_box
    from app.llm import _usage_context, clear_usage_context, set_usage_context
    from app.services.execution.tool_executor import (
        ToolExecutionContext,
        UnifiedToolExecutor,
    )

    init_llm_usage_table()
    seen: list[dict | None] = []

    async def _fake_execute_tool(tool_name, **kwargs):  # noqa: ANN001
        seen.append(_usage_context.get())
        _record()
        return {"success": True}

    monkeypatch.setattr(tool_box, "execute_tool", _fake_execute_tool)

    token = set_usage_context(
        session_id="session-x",
        plan_id=7,
        task_id=3,
        call_purpose="plan_task_execution",
        phase="plan",
        run_id="plan_7_task_3",
    )
    try:
        payload = await asyncio.to_thread(
            lambda: UnifiedToolExecutor().execute_sync(
                "code_executor",
                {"task": "write the report"},
                context=ToolExecutionContext(
                    plan_id=7, task_id=3, session_id="session-x"
                ),
            )
        )
    finally:
        clear_usage_context(token)

    assert payload["success"] is True
    assert seen and seen[0] is not None
    assert seen[0]["run_id"] == "plan_7_task_3"

    rows = _usage_rows()
    assert len(rows) == 1
    assert rows[0]["run_id"] == _CHILD_RUN_ID
    assert rows[0]["parent_run_id"] == "plan_7_task_3"
