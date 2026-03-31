"""ToolBox terminal session management tool."""

from __future__ import annotations

import base64
from typing import Any, Dict, Optional

from app.services.terminal.session_manager import terminal_session_manager
from app.services.terminal.ssh_backend import SSHConfig


async def terminal_session_handler(
    operation: str,
    session_id: Optional[str] = None,
    terminal_id: Optional[str] = None,
    mode: str = "sandbox",
    ssh_config: Optional[Dict[str, Any]] = None,
    data: Optional[str] = None,
    encoding: str = "utf-8",
    cols: Optional[int] = None,
    rows: Optional[int] = None,
    approval_id: Optional[str] = None,
    approved: Optional[bool] = None,
    limit: int = 500,
    start_ts: Optional[float] = None,
    end_ts: Optional[float] = None,
    event_type: Optional[str] = None,
) -> Dict[str, Any]:
    op = str(operation or "").strip().lower()

    if op == "create":
        if not session_id:
            raise ValueError("session_id is required for create")
        ssh_cfg = SSHConfig(**ssh_config) if (mode == "ssh" and isinstance(ssh_config, dict)) else None
        session = await terminal_session_manager.create_session(
            session_id,
            mode="ssh" if mode == "ssh" else "sandbox",
            ssh_config=ssh_cfg,
        )
        return {
            "operation": op,
            "success": True,
            "terminal_id": session.terminal_id,
            "session_id": session.session_id,
            "mode": session.mode,
            "state": session.state,
            "cwd": session.cwd,
        }

    if op == "ensure":
        if not session_id:
            raise ValueError("session_id is required for ensure")
        session = await terminal_session_manager.ensure_session_for_chat(
            session_id,
            mode="ssh" if mode == "ssh" else "sandbox",
        )
        return {
            "operation": op,
            "success": True,
            "terminal_id": session.terminal_id,
            "session_id": session.session_id,
            "mode": session.mode,
            "state": session.state,
            "cwd": session.cwd,
        }

    if op == "list":
        items = await terminal_session_manager.list_sessions(session_id=session_id)
        return {"operation": op, "success": True, "items": items, "count": len(items)}

    if op == "close":
        if not terminal_id:
            raise ValueError("terminal_id is required for close")
        await terminal_session_manager.close_session(terminal_id)
        return {"operation": op, "success": True, "terminal_id": terminal_id}

    if op == "write":
        if not terminal_id:
            raise ValueError("terminal_id is required for write")
        if data is None:
            payload = b""
        elif str(encoding).lower() == "base64":
            payload = base64.b64decode(str(data).encode("ascii"), validate=False)
        else:
            payload = str(data).encode("utf-8")
        result = await terminal_session_manager.write_and_wait(
            terminal_id,
            payload,
            timeout=10.0,
            idle_timeout=0.5,
        )
        settled = result["settled"]
        return {
            "operation": op,
            "success": True,
            "terminal_id": terminal_id,
            "bytes_sent": len(payload),
            "output": result["output"],
            # `status` is PTY settle state only: "completed" means no recent output for idle_timeout,
            # not shell exit success. Chat-side mutation verification uses verification_state.
            "status": "completed" if settled else "running",
            "message": None if settled else "Command still running. Use replay to check output later.",
            "command_state": "unverified",
            "verification_state": "not_attempted",
            "exit_code": None,
            "verification_summary": None,
            "verification_evidence": None,
        }

    if op == "resize":
        if not terminal_id:
            raise ValueError("terminal_id is required for resize")
        if cols is None or rows is None:
            raise ValueError("cols and rows are required for resize")
        await terminal_session_manager.resize(terminal_id, int(cols), int(rows))
        return {
            "operation": op,
            "success": True,
            "terminal_id": terminal_id,
            "cols": int(cols),
            "rows": int(rows),
        }

    if op == "approve":
        if not terminal_id or not approval_id:
            raise ValueError("terminal_id and approval_id are required for approve")
        ok = await terminal_session_manager.resolve_approval(
            terminal_id,
            approval_id,
            approved=True if approved is None else bool(approved),
        )
        return {
            "operation": op,
            "success": ok,
            "terminal_id": terminal_id,
            "approval_id": approval_id,
        }

    if op == "reject":
        if not terminal_id or not approval_id:
            raise ValueError("terminal_id and approval_id are required for reject")
        ok = await terminal_session_manager.resolve_approval(
            terminal_id,
            approval_id,
            approved=False,
        )
        return {
            "operation": op,
            "success": ok,
            "terminal_id": terminal_id,
            "approval_id": approval_id,
        }

    if op == "pending_approvals":
        if not terminal_id:
            raise ValueError("terminal_id is required for pending_approvals")
        items = await terminal_session_manager.pending_approvals(terminal_id)
        return {
            "operation": op,
            "success": True,
            "terminal_id": terminal_id,
            "items": items,
            "count": len(items),
        }

    if op == "replay":
        if not terminal_id:
            raise ValueError("terminal_id is required for replay")
        replay = await terminal_session_manager.get_replay(terminal_id, limit=max(1, int(limit)))
        return {
            "operation": op,
            "success": True,
            "terminal_id": terminal_id,
            "replay": replay,
            "count": len(replay),
        }

    if op == "audit":
        if not terminal_id:
            raise ValueError("terminal_id is required for audit")
        rows = await terminal_session_manager.query_audit(
            terminal_id,
            start_ts=start_ts,
            end_ts=end_ts,
            event_type=event_type,
            limit=max(1, int(limit)),
        )
        return {
            "operation": op,
            "success": True,
            "terminal_id": terminal_id,
            "items": rows,
            "count": len(rows),
        }

    raise ValueError(
        "Unknown operation. Supported operations: create, ensure, list, close, write, resize, "
        "approve, reject, pending_approvals, replay, audit"
    )


terminal_session_tool = {
    "name": "terminal_session",
    "description": "Create/manage interactive terminal sessions (sandbox PTY or SSH), including write/resize/audit/replay/approval operations.",
    "category": "execution",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "create",
                    "ensure",
                    "list",
                    "close",
                    "write",
                    "resize",
                    "approve",
                    "reject",
                    "pending_approvals",
                    "replay",
                    "audit",
                ],
            },
            "session_id": {"type": "string"},
            "terminal_id": {"type": "string"},
            "mode": {"type": "string", "enum": ["sandbox", "ssh"], "default": "sandbox"},
            "ssh_config": {
                "type": "object",
                "properties": {
                    "host": {"type": "string"},
                    "user": {"type": "string"},
                    "port": {"type": "integer", "default": 22},
                    "ssh_key_path": {"type": "string"},
                    "password": {"type": "string"},
                    "connect_timeout": {"type": "integer", "default": 15},
                },
            },
            "data": {"type": "string"},
            "encoding": {"type": "string", "enum": ["utf-8", "base64"], "default": "utf-8"},
            "cols": {"type": "integer"},
            "rows": {"type": "integer"},
            "approval_id": {"type": "string"},
            "approved": {"type": "boolean"},
            "limit": {"type": "integer", "default": 500},
            "start_ts": {"type": "number"},
            "end_ts": {"type": "number"},
            "event_type": {"type": "string"},
        },
        "required": ["operation"],
    },
    "handler": terminal_session_handler,
    "tags": ["terminal", "pty", "ssh", "interactive", "execution"],
    "examples": [
        "Create sandbox terminal for a chat session",
        "Write command input to terminal session",
        "Replay terminal output events for a session",
    ],
}
