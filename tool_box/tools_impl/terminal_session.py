"""ToolBox terminal session management tool."""

from __future__ import annotations

import base64
import re
from typing import Any, Dict, Optional

from tool_box.context import ToolContext

from app.services.terminal.session_manager import terminal_session_manager
from app.services.terminal.ssh_backend import SSHConfig


def _sanitize_session_owner(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip())
    token = token.strip("-._")
    return token or "terminal"


def _resolve_effective_session_id(
    session_id: Optional[str],
    tool_context: Optional[ToolContext],
) -> Optional[str]:
    explicit = str(session_id or "").strip()
    if explicit:
        return explicit

    if tool_context is not None:
        context_session = str(getattr(tool_context, "session_id", "") or "").strip()
        if context_session:
            return context_session

        plan_id = getattr(tool_context, "plan_id", None)
        task_id = getattr(tool_context, "task_id", None)
        job_id = str(getattr(tool_context, "job_id", "") or "").strip()
        if job_id or plan_id is not None or task_id is not None:
            parts = []
            if plan_id is not None:
                parts.append(f"plan{plan_id}")
            if task_id is not None:
                parts.append(f"task{task_id}")
            if job_id:
                job_token = _sanitize_session_owner(job_id)
                parts.append(job_token if job_token.startswith("job") else f"job{job_token}")
            return "_".join(parts) or None

    return None


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
    tool_context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    op = str(operation or "").strip().lower()
    effective_session_id = _resolve_effective_session_id(session_id, tool_context)

    if op == "create":
        if not effective_session_id:
            raise ValueError("session_id is required for create")
        resolved_mode = mode if mode in ("sandbox", "ssh", "qwen_code") else "sandbox"
        if resolved_mode == "ssh" and isinstance(ssh_config, dict):
            ssh_cfg = SSHConfig(**ssh_config)
            session = await terminal_session_manager.create_session(
                effective_session_id,
                mode=resolved_mode,
                ssh_config=ssh_cfg,
            )
        else:
            session = await terminal_session_manager.ensure_session_for_chat(
                effective_session_id,
                mode=resolved_mode,
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
        if not effective_session_id:
            raise ValueError("session_id is required for ensure")
        resolved_mode = mode if mode in ("sandbox", "ssh", "qwen_code") else "sandbox"
        session = await terminal_session_manager.ensure_session_for_chat(
            effective_session_id,
            mode=resolved_mode,
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
        items = await terminal_session_manager.list_sessions(session_id=effective_session_id)
        return {"operation": op, "success": True, "items": items, "count": len(items)}

    if op == "close":
        if not terminal_id:
            raise ValueError("terminal_id is required for close")
        await terminal_session_manager.close_session(terminal_id)
        return {"operation": op, "success": True, "terminal_id": terminal_id}

    if op == "write":
        resolved_terminal_id = str(terminal_id or "").strip()
        if not resolved_terminal_id:
            if not effective_session_id:
                raise ValueError("terminal_id is required for write")
            resolved_mode = mode if mode in ("sandbox", "ssh", "qwen_code") else "sandbox"
            session = await terminal_session_manager.ensure_session_for_chat(
                effective_session_id,
                mode=resolved_mode,
            )
            resolved_terminal_id = session.terminal_id
        if data is None:
            payload = b""
        elif str(encoding).lower() == "base64":
            payload = base64.b64decode(str(data).encode("ascii"), validate=False)
        else:
            payload = str(data).encode("utf-8")
        result = await terminal_session_manager.write_and_wait(
            resolved_terminal_id,
            payload,
            timeout=10.0,
            idle_timeout=0.5,
        )
        settled = result["settled"]
        return {
            "operation": op,
            "success": True,
            "terminal_id": resolved_terminal_id,
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
    "description": "Create/manage interactive terminal sessions (sandbox PTY or SSH), including write/resize/audit/replay/approval operations. In orchestrated executions, create/ensure/write can auto-bind to the current execution session.",
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
            "mode": {"type": "string", "enum": ["sandbox", "ssh", "qwen_code"], "default": "sandbox"},
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
