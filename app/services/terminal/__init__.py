"""Terminal services package."""

from .audit_logger import AuditLogger
from .command_filter import CommandDecision, CommandFilter, RiskLevel
from .protocol import WSMessage, WSMessageType, decode_bytes, encode_bytes, make_error_payload
from .pty_backend import PTYBackend
from .resource_limiter import DEFAULT_TERMINAL_LIMITS, ResourceLimits, apply_limits_in_child
from .session_manager import TerminalSession, TerminalSessionManager, terminal_session_manager
from .ssh_backend import SSHBackend, SSHConfig

__all__ = [
    "AuditLogger",
    "CommandDecision",
    "CommandFilter",
    "RiskLevel",
    "WSMessage",
    "WSMessageType",
    "decode_bytes",
    "encode_bytes",
    "make_error_payload",
    "PTYBackend",
    "DEFAULT_TERMINAL_LIMITS",
    "ResourceLimits",
    "apply_limits_in_child",
    "TerminalSession",
    "TerminalSessionManager",
    "terminal_session_manager",
    "SSHBackend",
    "SSHConfig",
]
