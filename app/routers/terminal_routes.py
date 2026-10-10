"""Terminal API and WebSocket routes."""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from app.database_pool import get_db
from app.middleware.proxy_auth import resolve_websocket_principal, websocket_access_allowed
from app.services.auth import legacy_proxy_access_allowed
from app.services.request_principal import (
    LEGACY_LOCAL_OWNER_ID,
    RequestPrincipal,
    get_request_principal,
)
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

_WS_CLOSE_UNAUTHENTICATED = 1008
_WS_CLOSE_FORBIDDEN = 1008


def _terminal_enabled() -> bool:
    # Default OFF (LOCAL_INFRA §120): the feature hands out an interactive shell
    # and must be an explicit opt-in per deployment.
    raw = str(os.getenv("TERMINAL_ENABLED", "false")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _require_terminal_enabled() -> None:
    if not _terminal_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Terminal feature is disabled",
        )


# ---------------------------------------------------------------- ownership
def _tenancy_enforced(principal: RequestPrincipal) -> bool:
    """Legacy (single-tenant proxy fallback) principals have no ownership model."""
    if principal.is_authenticated:
        return True
    return not legacy_proxy_access_allowed(principal)


def _session_owner_id(session_id: str) -> Optional[str]:
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT owner_id FROM chat_sessions WHERE id=?",
                (session_id,),
            ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    return str(row["owner_id"] or "").strip() or LEGACY_LOCAL_OWNER_ID


def _session_owned_by(principal: RequestPrincipal, session_id: str) -> bool:
    if not _tenancy_enforced(principal):
        return True
    owner = _session_owner_id(session_id)
    if owner is None:
        return False
    return owner == principal.owner_id


def _require_session_owner(request: Request, session_id: str) -> RequestPrincipal:
    principal = get_request_principal(request)
    if not _session_owned_by(principal, session_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="session owner mismatch")
    return principal


async def _require_terminal_owner(request: Request, terminal_id: str) -> RequestPrincipal:
    principal = get_request_principal(request)
    if not _tenancy_enforced(principal):
        return principal
    try:
        session = await terminal_session_manager.get_session(terminal_id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Terminal not found") from exc
    if not _session_owned_by(principal, session.session_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="terminal owner mismatch")
    return principal


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
async def list_terminal_sessions(request: Request, session_id: Optional[str] = Query(None)):
    _require_terminal_enabled()
    principal = get_request_principal(request)
    if session_id:
        _require_session_owner(request, session_id)
    rows = await terminal_session_manager.list_sessions(session_id=session_id)
    if _tenancy_enforced(principal):
        rows = [row for row in rows if _session_owned_by(principal, str(row.get("session_id") or ""))]
    return [TerminalSessionResponse(**row) for row in rows]


@router.post("/api/v1/terminal/sessions", response_model=TerminalSessionResponse)
async def create_terminal_session(request: Request, payload: CreateTerminalSessionRequest):
    _require_terminal_enabled()
    _require_session_owner(request, payload.session_id)
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
async def close_terminal_session(request: Request, terminal_id: str):
    _require_terminal_enabled()
    await _require_terminal_owner(request, terminal_id)
    await terminal_session_manager.close_session(terminal_id)
    return {"success": True, "terminal_id": terminal_id}


@router.get("/api/v1/terminal/sessions/{terminal_id}/replay")
async def get_terminal_replay(
    request: Request,
    terminal_id: str,
    limit: int = Query(4000, ge=1, le=20000),
):
    _require_terminal_enabled()
    await _require_terminal_owner(request, terminal_id)
    replay = await terminal_session_manager.get_replay(terminal_id, limit=limit)
    return replay


@router.get("/api/v1/terminal/audit")
async def get_terminal_audit(
    request: Request,
    terminal_id: str = Query(..., min_length=1),
    start_ts: Optional[float] = Query(None),
    end_ts: Optional[float] = Query(None),
    event_type: Optional[str] = Query(None),
    limit: int = Query(500, ge=1, le=5000),
):
    _require_terminal_enabled()
    await _require_terminal_owner(request, terminal_id)
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

    # Authenticate BEFORE accept(): the middleware already rejects anonymous
    # handshakes, this is defence in depth for routers mounted without it.
    if not websocket_access_allowed(websocket):
        await websocket.close(code=_WS_CLOSE_UNAUTHENTICATED, reason="Authentication required.")
        return
    principal = resolve_websocket_principal(websocket)
    if not _session_owned_by(principal, session_id):
        await websocket.close(code=_WS_CLOSE_FORBIDDEN, reason="session owner mismatch")
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
            if session.mode != mode:
                session = await terminal_session_manager.ensure_session_for_chat(session_id, mode=mode)
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
