"""Terminal WebSocket protocol models and helpers."""

from __future__ import annotations

import base64
import time
from enum import Enum
from typing import Any, Dict

from pydantic import BaseModel, Field


class WSMessageType(str, Enum):
    # Client -> Server
    INPUT = "input"
    RESIZE = "resize"
    PING = "ping"
    CMD_APPROVE = "cmd_approve"
    CMD_REJECT = "cmd_reject"

    # Server -> Client
    OUTPUT = "output"
    APPROVAL_REQUIRED = "approval"
    SESSION_CLOSED = "closed"
    ERROR = "error"
    PONG = "pong"


class WSMessage(BaseModel):
    type: WSMessageType
    payload: Any = None
    timestamp: float = Field(default_factory=lambda: time.time() * 1000)


def encode_bytes(data: bytes) -> str:
    """Encode bytes for WebSocket text payloads."""
    if not data:
        return ""
    return base64.b64encode(data).decode("ascii")


def decode_bytes(data: str) -> bytes:
    """Decode bytes from WebSocket payloads."""
    if not data:
        return b""
    return base64.b64decode(data.encode("ascii"), validate=False)


def make_error_payload(message: str, *, code: str = "TERMINAL_ERROR") -> Dict[str, str]:
    return {"message": str(message), "code": code}
