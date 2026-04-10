"""Lifecycle management for terminal sessions."""

from __future__ import annotations

import asyncio
import base64
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Set, Union
from uuid import uuid4

from .audit_logger import AuditLogger
from .command_filter import CommandFilter, CommandDecision, RiskLevel
from .docker_pty_backend import DockerPTYBackend
from .protocol import WSMessageType
from .pty_backend import PTYBackend
from .ssh_backend import SSHBackend, SSHConfig

try:  # pragma: no cover - optional import path for remote defaults
    from tool_box.bio_tools.remote_executor import RemoteExecutionConfig
except Exception:  # pragma: no cover
    RemoteExecutionConfig = None  # type: ignore


TerminalMode = Literal["sandbox", "ssh", "qwen_code"]


@dataclass
class TerminalEvent:
    type: WSMessageType
    payload: Any = None


@dataclass
class PendingApproval:
    approval_id: str
    command: str
    risk_level: str
    reason: str
    created_at: float
    future: asyncio.Future


@dataclass
class TerminalSession:
    session_id: str
    terminal_id: str
    mode: TerminalMode
    backend: Union[PTYBackend, SSHBackend, DockerPTYBackend]
    created_at: datetime
    last_activity: datetime
    state: Literal["creating", "active", "idle", "closing", "closed"]
    env: Dict[str, str]
    cwd: str
    audit_logger: AuditLogger
    subscribers: Set[asyncio.Queue] = field(default_factory=set)
    pending_approvals: Dict[str, PendingApproval] = field(default_factory=dict)
    output_task: Optional[asyncio.Task] = None


class TerminalSessionManager:
    def __init__(self, *, max_sessions: int = 10, idle_timeout: int = 1800) -> None:
        self._sessions: Dict[str, TerminalSession] = {}
        self._max_sessions = max_sessions
        self._idle_timeout = idle_timeout
        self._lock = asyncio.Lock()
        self._command_filter = CommandFilter()
        self._reaper_task: Optional[asyncio.Task] = None

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _ensure_reaper(self) -> None:
        if self._reaper_task is not None and not self._reaper_task.done():
            return
        loop = asyncio.get_running_loop()
        self._reaper_task = loop.create_task(self._reap_idle_sessions())

    def _workspace_path(self, owner: str) -> Path:
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", owner).strip("-._") or "workspace"
        root = Path(os.getenv("EXECUTION_WORKSPACES_ROOT", "runtime/workspaces")).resolve()
        root.mkdir(parents=True, exist_ok=True)
        path = (root / slug[:128]).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def create_session(
        self,
        session_id: str,
        *,
        mode: TerminalMode = "sandbox",
        ssh_config: Optional[SSHConfig] = None,
    ) -> TerminalSession:
        self._ensure_reaper()
        owner = str(session_id).strip()
        if not owner:
            raise ValueError("session_id is required")

        async with self._lock:
            active_count = sum(1 for s in self._sessions.values() if s.state != "closed")
            if active_count >= self._max_sessions:
                raise ValueError("Maximum number of terminal sessions reached")

        workspace = self._workspace_path(owner)
        terminal_id = str(uuid4())
        created_at = self._now()
        backend: Union[PTYBackend, SSHBackend, DockerPTYBackend]
        audit_logger = AuditLogger(terminal_id)

        if mode == "sandbox":
            backend = PTYBackend()
        elif mode == "ssh":
            backend = SSHBackend()
        elif mode == "qwen_code":
            backend = DockerPTYBackend()
        else:
            raise ValueError(f"Unsupported terminal mode: {mode}")

        session = TerminalSession(
            session_id=owner,
            terminal_id=terminal_id,
            mode=mode,
            backend=backend,
            created_at=created_at,
            last_activity=created_at,
            state="creating",
            env={},
            cwd=str(Path(workspace).resolve()),
            audit_logger=audit_logger,
        )

        if mode == "sandbox":
            await backend.spawn(
                shell="/bin/bash",
                cwd=session.cwd,
                env=session.env,
                command_handler=lambda command: self._handle_command_check(session, command),
            )
        elif mode == "qwen_code":
            await backend.spawn(
                cwd=session.cwd,
                env=session.env,
            )
        else:
            cfg = ssh_config
            if cfg is None:
                if RemoteExecutionConfig is None:
                    raise RuntimeError("RemoteExecutionConfig is unavailable")
                remote = RemoteExecutionConfig.from_env()
                cfg = SSHConfig.from_remote_execution_config(remote)
            await backend.connect(cfg)

        session.state = "active"
        session.audit_logger.log_event(
            "session_created",
            metadata={
                "session_id": session.session_id,
                "terminal_id": session.terminal_id,
                "mode": session.mode,
                "cwd": session.cwd,
            },
        )

        session.output_task = asyncio.create_task(self._pump_output(session))

        async with self._lock:
            self._sessions[session.terminal_id] = session

        return session

    async def ensure_session_for_chat(
        self,
        session_id: str,
        *,
        mode: TerminalMode = "sandbox",
    ) -> TerminalSession:
        target_id = str(session_id).strip()
        if not target_id:
            raise ValueError("session_id is required")

        async with self._lock:
            candidates = [
                s
                for s in self._sessions.values()
                if s.session_id == target_id and s.mode == mode and s.state in {"active", "idle", "creating"}
            ]
            if candidates:
                candidates.sort(key=lambda s: s.last_activity, reverse=True)
                return candidates[0]

        return await self.create_session(target_id, mode=mode)

    async def list_sessions(self, *, session_id: Optional[str] = None) -> list[dict[str, Any]]:
        async with self._lock:
            rows = []
            for s in self._sessions.values():
                if session_id and s.session_id != session_id:
                    continue
                rows.append(
                    {
                        "terminal_id": s.terminal_id,
                        "session_id": s.session_id,
                        "mode": s.mode,
                        "state": s.state,
                        "cwd": s.cwd,
                        "created_at": s.created_at.isoformat(),
                        "last_activity": s.last_activity.isoformat(),
                        "pending_approvals": len(s.pending_approvals),
                    }
                )
            rows.sort(key=lambda item: item["created_at"], reverse=True)
            return rows

    async def get_session(self, terminal_id: str) -> TerminalSession:
        async with self._lock:
            session = self._sessions.get(terminal_id)
            if session is None:
                raise KeyError(f"Unknown terminal_id: {terminal_id}")
            return session

    async def subscribe(self, terminal_id: str) -> asyncio.Queue:
        session = await self.get_session(terminal_id)
        queue: asyncio.Queue = asyncio.Queue(maxsize=4096)

        # Replay recent output to restore terminal context after refresh.
        replay_events = session.audit_logger.query_events(event_type="output", limit=400)
        for item in replay_events:
            encoded = item.get("data") or ""
            if not encoded:
                continue
            try:
                payload = base64.b64decode(encoded.encode("ascii"), validate=False)
            except Exception:
                continue
            queue.put_nowait(TerminalEvent(type=WSMessageType.OUTPUT, payload=payload))

        async with self._lock:
            session.subscribers.add(queue)
            session.state = "active"
            session.last_activity = self._now()

        return queue

    async def unsubscribe(self, terminal_id: str, queue: asyncio.Queue) -> None:
        session = await self.get_session(terminal_id)
        async with self._lock:
            session.subscribers.discard(queue)
            session.last_activity = self._now()
            if not session.subscribers and session.state not in {"closing", "closed"}:
                session.state = "idle"

    async def write(self, terminal_id: str, data: bytes) -> None:
        session = await self.get_session(terminal_id)
        session.last_activity = self._now()
        session.audit_logger.log_event("input", data=data)
        await session.backend.write(data)

    async def write_and_wait(
        self,
        terminal_id: str,
        data: bytes,
        *,
        timeout: float = 10.0,
        idle_timeout: float = 0.5,
    ) -> dict[str, Any]:
        """Write data and wait for command output.

        Subscribes to the output stream, waits until output settles
        (no new data for *idle_timeout* seconds) or *timeout* is reached.
        Returns collected output and whether the command appears to have
        finished (settled) or is still running (timed out).
        """
        session = await self.get_session(terminal_id)
        queue: asyncio.Queue = asyncio.Queue(maxsize=2048)
        session.subscribers.add(queue)
        try:
            session.last_activity = self._now()
            session.audit_logger.log_event("input", data=data)
            await session.backend.write(data)

            chunks: list[bytes] = []
            deadline = asyncio.get_event_loop().time() + timeout
            settled = False

            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                wait_time = min(remaining, idle_timeout)
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=wait_time)
                except asyncio.TimeoutError:
                    # No output for idle_timeout — command likely finished
                    if chunks:
                        settled = True
                        break
                    # Haven't seen any output yet; keep waiting up to deadline
                    continue
                if isinstance(event, TerminalEvent) and event.type == WSMessageType.OUTPUT:
                    if isinstance(event.payload, (bytes, bytearray)):
                        chunks.append(bytes(event.payload))
                    elif isinstance(event.payload, str):
                        chunks.append(event.payload.encode("utf-8", errors="replace"))

            raw = b"".join(chunks)
            text = raw.decode("utf-8", errors="replace")
            # Truncate to avoid bloating LLM context
            if len(text) > 8000:
                text = text[:2000] + "\n... (truncated) ...\n" + text[-4000:]
            return {
                "output": text,
                "settled": settled,
                "bytes_received": len(raw),
            }
        finally:
            session.subscribers.discard(queue)

    async def resize(self, terminal_id: str, cols: int, rows: int) -> None:
        session = await self.get_session(terminal_id)
        session.last_activity = self._now()
        session.audit_logger.log_event(
            "resize",
            metadata={"cols": int(cols), "rows": int(rows)},
        )
        await session.backend.resize(int(cols), int(rows))

    async def close_session(self, terminal_id: str) -> None:
        async with self._lock:
            session = self._sessions.get(terminal_id)
            if session is None:
                return
            session.state = "closing"

        if session.output_task:
            session.output_task.cancel()
            try:
                await session.output_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        for pending in list(session.pending_approvals.values()):
            if not pending.future.done():
                pending.future.set_result(False)
        session.pending_approvals.clear()

        if isinstance(session.backend, (PTYBackend, DockerPTYBackend)):
            await session.backend.terminate()
        else:
            await session.backend.disconnect()

        session.audit_logger.log_event(
            "session_closed",
            metadata={
                "session_id": session.session_id,
                "terminal_id": session.terminal_id,
                "mode": session.mode,
            },
        )

        await self._broadcast(session, TerminalEvent(type=WSMessageType.SESSION_CLOSED, payload={"terminal_id": terminal_id}))

        async with self._lock:
            session.state = "closed"
            session.last_activity = self._now()
            self._sessions.pop(terminal_id, None)

        session.audit_logger.close()

    async def resolve_approval(self, terminal_id: str, approval_id: str, approved: bool) -> bool:
        session = await self.get_session(terminal_id)
        pending = session.pending_approvals.get(approval_id)
        if pending is None:
            return False
        if pending.future.done():
            return True
        pending.future.set_result(bool(approved))
        return True

    async def pending_approvals(self, terminal_id: str) -> list[dict[str, Any]]:
        session = await self.get_session(terminal_id)
        out = []
        for item in session.pending_approvals.values():
            out.append(
                {
                    "approval_id": item.approval_id,
                    "command": item.command,
                    "risk_level": item.risk_level,
                    "reason": item.reason,
                    "created_at": item.created_at,
                }
            )
        return out

    async def get_replay(self, terminal_id: str, *, limit: int = 4000) -> list[dict[str, Any]]:
        session = await self.get_session(terminal_id)
        return session.audit_logger.build_replay(limit=limit, include_input=True)

    async def query_audit(
        self,
        terminal_id: str,
        *,
        start_ts: Optional[float] = None,
        end_ts: Optional[float] = None,
        event_type: Optional[str] = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        session = await self.get_session(terminal_id)
        return session.audit_logger.query_events(
            start_ts=start_ts,
            end_ts=end_ts,
            event_type=event_type,
            limit=limit,
        )

    async def _pump_output(self, session: TerminalSession) -> None:
        try:
            while session.state not in {"closing", "closed"}:
                chunk = await session.backend.read()
                if not chunk:
                    if getattr(session.backend, "is_closed", False):
                        break
                    continue
                session.last_activity = self._now()
                session.audit_logger.log_event("output", data=chunk)
                await self._broadcast(session, TerminalEvent(type=WSMessageType.OUTPUT, payload=chunk))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            session.audit_logger.log_event(
                "error",
                metadata={"message": str(exc), "component": "output_pump"},
            )
            await self._broadcast(
                session,
                TerminalEvent(
                    type=WSMessageType.ERROR,
                    payload={"message": str(exc), "code": "OUTPUT_PUMP_ERROR"},
                ),
            )

    async def _broadcast(self, session: TerminalSession, event: TerminalEvent) -> None:
        if not session.subscribers:
            return
        stale: list[asyncio.Queue] = []
        for queue in list(session.subscribers):
            try:
                if queue.full():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                queue.put_nowait(event)
            except Exception:
                stale.append(queue)
        if stale:
            async with self._lock:
                for queue in stale:
                    session.subscribers.discard(queue)

    async def _handle_command_check(self, session: TerminalSession, command: str) -> str:
        decision: CommandDecision = self._command_filter.classify(command)
        metadata = {
            "command": command,
            "risk_level": decision.risk_level.value,
            "reason": decision.reason,
            "requires_approval": decision.requires_approval,
        }
        session.audit_logger.log_event("command_detected", metadata=metadata)

        if decision.risk_level == RiskLevel.FORBIDDEN:
            approval_id = str(uuid4())
            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            pending = PendingApproval(
                approval_id=approval_id,
                command=command,
                risk_level=decision.risk_level.value,
                reason=decision.reason,
                created_at=time.time(),
                future=fut,
            )
            session.pending_approvals[approval_id] = pending

            await self._broadcast(
                session,
                TerminalEvent(
                    type=WSMessageType.APPROVAL_REQUIRED,
                    payload={
                        "approval_id": approval_id,
                        "command": command,
                        "risk_level": decision.risk_level.value,
                        "reason": decision.reason,
                    },
                ),
            )

            approved = False
            try:
                approved = bool(await asyncio.wait_for(fut, timeout=300))
            except asyncio.TimeoutError:
                approved = False
            finally:
                session.pending_approvals.pop(approval_id, None)

            if approved:
                session.audit_logger.log_event(
                    "command_approved",
                    metadata={"approval_id": approval_id, **metadata},
                )
                return "ALLOW"

            session.audit_logger.log_event(
                "command_rejected",
                metadata={"approval_id": approval_id, **metadata},
            )
            return "BLOCK"

        if decision.risk_level == RiskLevel.ELEVATED:
            session.audit_logger.log_event("command_elevated", metadata=metadata)
        return "ALLOW"

    async def _reap_idle_sessions(self) -> None:
        while True:
            try:
                await asyncio.sleep(30)
                now = self._now()
                to_close: list[str] = []
                async with self._lock:
                    for terminal_id, session in self._sessions.items():
                        if session.state != "idle":
                            continue
                        idle_seconds = (now - session.last_activity).total_seconds()
                        if idle_seconds >= self._idle_timeout:
                            to_close.append(terminal_id)
                for terminal_id in to_close:
                    await self.close_session(terminal_id)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(2)


terminal_session_manager = TerminalSessionManager()
