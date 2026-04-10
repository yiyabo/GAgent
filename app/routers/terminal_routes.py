"""Terminal API and WebSocket routes."""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from app.services.terminal import (
    WSMessage,
    WSMessageType,
    decode_bytes,
    encode_bytes,
    make_error_payload,
    terminal_session_manager,
)
from app.services.terminal.session_manager import TerminalEvent
from app.services.terminal.ssh_backend import SSHConfig
from . import register_router


router = APIRouter(tags=["terminal"])


def _terminal_enabled() -> bool:
    raw = str(os.getenv("TERMINAL_ENABLED", "true")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _require_terminal_enabled() -> None:
    if not _terminal_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Terminal feature is disabled",
        )


class SSHConfigPayload(BaseModel):
    host: str
    user: str
    port: int = 22
    ssh_key_path: Optional[str] = None
    password: Optional[str] = None
    connect_timeout: int = 15


class CreateTerminalSessionRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    mode: Literal["sandbox", "ssh", "qwen_code"] = "sandbox"
    ssh_config: Optional[SSHConfigPayload] = None


class TerminalSessionResponse(BaseModel):
    terminal_id: str
    session_id: str
    mode: str
    state: str
    cwd: str
    created_at: str
    last_activity: str
    pending_approvals: int


@router.get("/api/v1/terminal/sessions", response_model=List[TerminalSessionResponse])
async def list_terminal_sessions(session_id: Optional[str] = Query(None)):
    _require_terminal_enabled()
    rows = await terminal_session_manager.list_sessions(session_id=session_id)
    return [TerminalSessionResponse(**row) for row in rows]


@router.post("/api/v1/terminal/sessions", response_model=TerminalSessionResponse)
async def create_terminal_session(payload: CreateTerminalSessionRequest):
    _require_terminal_enabled()
    ssh_cfg = None
    if payload.mode == "ssh" and payload.ssh_config:
        ssh_cfg = SSHConfig(**payload.ssh_config.model_dump())

    session = await terminal_session_manager.create_session(
        payload.session_id,
        mode=payload.mode,
        ssh_config=ssh_cfg,
    )
    return TerminalSessionResponse(
        terminal_id=session.terminal_id,
        session_id=session.session_id,
        mode=session.mode,
        state=session.state,
        cwd=session.cwd,
        created_at=session.created_at.isoformat(),
        last_activity=session.last_activity.isoformat(),
        pending_approvals=len(session.pending_approvals),
    )


@router.delete("/api/v1/terminal/sessions/{terminal_id}")
async def close_terminal_session(terminal_id: str):
    _require_terminal_enabled()
    await terminal_session_manager.close_session(terminal_id)
    return {"success": True, "terminal_id": terminal_id}


@router.get("/api/v1/terminal/sessions/{terminal_id}/replay")
async def get_terminal_replay(
    terminal_id: str,
    limit: int = Query(4000, ge=1, le=20000),
):
    _require_terminal_enabled()
    replay = await terminal_session_manager.get_replay(terminal_id, limit=limit)
    return replay


@router.get("/api/v1/terminal/audit")
async def get_terminal_audit(
    terminal_id: str = Query(..., min_length=1),
    start_ts: Optional[float] = Query(None),
    end_ts: Optional[float] = Query(None),
    event_type: Optional[str] = Query(None),
    limit: int = Query(500, ge=1, le=5000),
):
    _require_terminal_enabled()
    rows = await terminal_session_manager.query_audit(
        terminal_id,
        start_ts=start_ts,
        end_ts=end_ts,
        event_type=event_type,
        limit=limit,
    )
    return rows


@router.websocket("/ws/terminal/{session_id}")
async def terminal_websocket(
    websocket: WebSocket,
    session_id: str,
    mode: Literal["sandbox", "ssh", "qwen_code"] = Query("sandbox"),
    terminal_id: Optional[str] = Query(None),
):
    if not _terminal_enabled():
        await websocket.close(code=1013, reason="Terminal disabled")
        return

    await websocket.accept()

    try:
        if terminal_id:
            session = await terminal_session_manager.get_session(terminal_id)
            # Verify the terminal belongs to the requesting chat session
            if session.session_id != session_id:
                await websocket.send_json(
                    WSMessage(
                        type=WSMessageType.ERROR,
                        payload=make_error_payload(
                            "Terminal does not belong to this session",
                            code="SESSION_MISMATCH",
                        ),
                    ).model_dump()
                )
                await websocket.close(code=1011)
                return
        else:
            session = await terminal_session_manager.ensure_session_for_chat(session_id, mode=mode)
    except Exception as exc:
        await websocket.send_json(
            WSMessage(
                type=WSMessageType.ERROR,
                payload=make_error_payload(str(exc), code="SESSION_CREATE_FAILED"),
            ).model_dump()
        )
        await websocket.close(code=1011)
        return

    queue = await terminal_session_manager.subscribe(session.terminal_id)

    await websocket.send_json(
        WSMessage(
            type=WSMessageType.PONG,
            payload={"terminal_id": session.terminal_id, "mode": session.mode},
        ).model_dump()
    )

    async def _sender() -> None:
        while True:
            event: TerminalEvent = await queue.get()
            if event.type == WSMessageType.OUTPUT:
                payload = encode_bytes(event.payload if isinstance(event.payload, (bytes, bytearray)) else b"")
            else:
                payload = event.payload
            await websocket.send_json(
                WSMessage(type=event.type, payload=payload).model_dump()
            )
            if event.type == WSMessageType.SESSION_CLOSED:
                break

    async def _receiver() -> None:
        while True:
            data = await websocket.receive_json()
            message = WSMessage.model_validate(data)

            if message.type == WSMessageType.INPUT:
                chunk = decode_bytes(str(message.payload or ""))
                await terminal_session_manager.write(session.terminal_id, chunk)
                continue

            if message.type == WSMessageType.RESIZE:
                payload = message.payload if isinstance(message.payload, dict) else {}
                cols = int(payload.get("cols", 120))
                rows = int(payload.get("rows", 36))
                await terminal_session_manager.resize(session.terminal_id, cols, rows)
                continue

            if message.type == WSMessageType.PING:
                await websocket.send_json(
                    WSMessage(type=WSMessageType.PONG, payload={"terminal_id": session.terminal_id}).model_dump()
                )
                continue

            if message.type in {WSMessageType.CMD_APPROVE, WSMessageType.CMD_REJECT}:
                payload = message.payload if isinstance(message.payload, dict) else {}
                approval_id = str(payload.get("approval_id") or "").strip()
                if approval_id:
                    await terminal_session_manager.resolve_approval(
                        session.terminal_id,
                        approval_id,
                        approved=message.type == WSMessageType.CMD_APPROVE,
                    )
                continue

    sender_task = asyncio.create_task(_sender())
    receiver_task = asyncio.create_task(_receiver())

    try:
        done, pending = await asyncio.wait(
            [sender_task, receiver_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                await websocket.send_json(
                    WSMessage(
                        type=WSMessageType.ERROR,
                        payload=make_error_payload(str(exc), code="TERMINAL_WS_ERROR"),
                    ).model_dump()
                )
    except WebSocketDisconnect:
        pass
    finally:
        await terminal_session_manager.unsubscribe(session.terminal_id, queue)


register_router(
    namespace="terminal",
    version="v1",
    path="/api/v1/terminal",
    router=router,
    tags=["terminal"],
    description="Interactive terminal sessions (PTY sandbox and SSH)",
)
