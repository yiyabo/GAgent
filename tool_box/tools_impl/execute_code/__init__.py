"""execute_code — Programmatic Tool Calling ("code mode") for GAgent.

Env-gated (CODE_MODE_ENABLED=1, default OFF). See design/2026-09-24-code-mode.md.
"""

from .config import allowed_tools, code_mode_enabled
from .tool import TOOL_NAME, build_description, execute_code_handler, execute_code_tool

__all__ = [
    "TOOL_NAME",
    "allowed_tools",
    "build_description",
    "code_mode_enabled",
    "execute_code_handler",
    "execute_code_tool",
]
