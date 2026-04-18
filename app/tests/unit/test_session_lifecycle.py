import asyncio

from app.services.terminal.protocol import WSMessageType
from app.services.terminal.session_manager import TerminalEvent, TerminalSessionManager


async def _run_case() -> None:
    manager = TerminalSessionManager(max_sessions=2, idle_timeout=2)
    session = await manager.create_session("session-lifecycle", mode="sandbox")
    queue = await manager.subscribe(session.terminal_id)

    await manager.write(session.terminal_id, b"echo lifecycle_ok\\n")

    seen = False
    for _ in range(40):
        event: TerminalEvent = await asyncio.wait_for(queue.get(), timeout=2)
        if event.type != WSMessageType.OUTPUT:
            continue
        payload = event.payload if isinstance(event.payload, (bytes, bytearray)) else b""
        if b"lifecycle_ok" in payload:
            seen = True
            break

    assert seen is True

    await manager.unsubscribe(session.terminal_id, queue)
    listed = await manager.list_sessions(session_id="session-lifecycle")
    assert listed and listed[0]["state"] in {"idle", "active"}

    replay = await manager.get_replay(session.terminal_id, limit=50)
    assert replay
    assert any(item["type"] == "o" for item in replay)

    await manager.close_session(session.terminal_id)

    if manager._reaper_task is not None:  # pylint: disable=protected-access
        manager._reaper_task.cancel()  # pylint: disable=protected-access


def test_session_lifecycle_basic_roundtrip() -> None:
    asyncio.run(_run_case())
