import asyncio

from tool_box.tools_impl.terminal_session import terminal_session_handler


async def _run_case() -> None:
    create = await terminal_session_handler(
        operation="create",
        session_id="tool-terminal-session",
        mode="sandbox",
    )
    assert create["success"] is True
    terminal_id = create["terminal_id"]

    listing = await terminal_session_handler(operation="list", session_id="tool-terminal-session")
    assert listing["success"] is True
    assert listing["count"] >= 1

    write_resp = await terminal_session_handler(
        operation="write",
        terminal_id=terminal_id,
        data="echo tool_session_ok\\n",
    )
    assert write_resp["success"] is True

    audit = await terminal_session_handler(
        operation="audit",
        terminal_id=terminal_id,
        limit=50,
    )
    assert audit["success"] is True

    close = await terminal_session_handler(operation="close", terminal_id=terminal_id)
    assert close["success"] is True


def test_terminal_session_tool_create_list_close() -> None:
    asyncio.run(_run_case())
