import asyncio
from types import SimpleNamespace

from tool_box.context import ToolContext
from tool_box.tools_impl.terminal_session import terminal_session_handler
from app.services.terminal.session_manager import terminal_session_manager


async def _run_case() -> None:
    create = await terminal_session_handler(
        operation="create",
        session_id="tool-terminal-session",
        mode="sandbox",
    )
    assert create["success"] is True
    assert create["operation"] == "create"
    terminal_id = create["terminal_id"]

    listing = await terminal_session_handler(operation="list", session_id="tool-terminal-session")
    assert listing["success"] is True
    assert listing["operation"] == "list"
    assert listing["count"] >= 1

    write_resp = await terminal_session_handler(
        operation="write",
        terminal_id=terminal_id,
        data="echo tool_session_ok\\n",
    )
    assert write_resp["success"] is True
    assert write_resp["operation"] == "write"

    audit = await terminal_session_handler(
        operation="audit",
        terminal_id=terminal_id,
        limit=50,
    )
    assert audit["success"] is True
    assert audit["operation"] == "audit"

    close = await terminal_session_handler(operation="close", terminal_id=terminal_id)
    assert close["success"] is True
    assert close["operation"] == "close"


def test_terminal_session_tool_create_list_close() -> None:
    asyncio.run(_run_case())


async def _run_context_fallback_case() -> None:
    ctx = ToolContext(plan_id=7, task_id=23, job_id="job-abc123")

    create = await terminal_session_handler(
        operation="create",
        mode="sandbox",
        tool_context=ctx,
    )
    assert create["success"] is True
    assert create["operation"] == "create"
    assert create["session_id"] == "plan7_task23_job-abc123"

    write_resp = await terminal_session_handler(
        operation="write",
        data="echo context_fallback_ok\n",
        tool_context=ctx,
    )
    assert write_resp["success"] is True
    assert write_resp["operation"] == "write"
    assert write_resp["terminal_id"] == create["terminal_id"]

    listing = await terminal_session_handler(operation="list", tool_context=ctx)
    assert listing["success"] is True
    assert listing["count"] >= 1

    close = await terminal_session_handler(operation="close", terminal_id=create["terminal_id"])
    assert close["success"] is True


def test_terminal_session_tool_context_fallback_create_and_write() -> None:
    asyncio.run(_run_context_fallback_case())


async def _run_ssh_create_path_case() -> None:
    calls: list[tuple[str, str, str, str]] = []

    async def _fake_create_session(session_id: str, *, mode: str = "sandbox", ssh_config=None, **_kwargs):
        calls.append(("create", session_id, mode, getattr(ssh_config, "host", "")))
        return SimpleNamespace(
            terminal_id="ssh-terminal",
            session_id=session_id,
            mode=mode,
            state="active",
            cwd="/tmp/ssh-terminal",
        )

    async def _fake_ensure_session_for_chat(*_args, **_kwargs):
        raise AssertionError("ssh create with ssh_config should not route through ensure_session_for_chat")

    original_create = terminal_session_manager.create_session
    original_ensure = terminal_session_manager.ensure_session_for_chat
    terminal_session_manager.create_session = _fake_create_session
    terminal_session_manager.ensure_session_for_chat = _fake_ensure_session_for_chat
    try:
        create = await terminal_session_handler(
            operation="create",
            session_id="ssh-session",
            mode="ssh",
            ssh_config={
                "host": "example.org",
                "user": "tester",
                "port": 22,
            },
        )
    finally:
        terminal_session_manager.create_session = original_create
        terminal_session_manager.ensure_session_for_chat = original_ensure

    assert create["success"] is True
    assert create["terminal_id"] == "ssh-terminal"
    assert calls == [("create", "ssh-session", "ssh", "example.org")]


def test_terminal_session_tool_ssh_create_uses_explicit_create_path() -> None:
    asyncio.run(_run_ssh_create_path_case())
