"""Command risk classification for terminal sessions."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from enum import Enum
from typing import List, Pattern


class RiskLevel(str, Enum):
    SAFE = "safe"
    ELEVATED = "elevated"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True)
class CommandDecision:
    command: str
    risk_level: RiskLevel
    reason: str
    requires_approval: bool


SAFE_COMMANDS = {
    "ls",
    "cat",
    "head",
    "tail",
    "less",
    "more",
    "grep",
    "find",
    "wc",
    "file",
    "which",
    "whoami",
    "pwd",
    "env",
    "echo",
    "date",
    "df",
    "du",
    "free",
    "top",
    "htop",
    "ps",
    "uptime",
    "uname",
}

SAFE_PATTERNS = [
    r"^pip\s+(list|show|freeze)",
    r"^conda\s+(list|info|env\s+list)",
    r"^git\s+(status|log|diff|show|branch)",
]

ELEVATED_COMMANDS = {
    "mkdir",
    "touch",
    "cp",
    "mv",
    "chmod",
    "chown",
    "pip",
    "conda",
    "npm",
    "yarn",
    "apt",
    "brew",
    "python",
    "docker",
    "git",
    "bash",
}

ELEVATED_PATTERNS = [
    r"^git\s+(add|commit|push|pull|merge|rebase)",
    r"^python\s+\S+\.py",
    r"^bash\s+\S+\.sh",
    r"^docker\s+(run|exec|build)",
    r">\s*\S+",
]

FORBIDDEN_COMMANDS = {
    "rm",
    "rmdir",
    "dd",
    "mkfs",
    "fdisk",
    "shutdown",
    "reboot",
    "halt",
    "poweroff",
    "kill",
    "killall",
    "pkill",
}

FORBIDDEN_PATTERNS = [
    r"rm\s+-[rRf]",
    r"curl.*\|\s*(bash|sh)",
    r"chmod\s+777",
    r"sudo\s+",
    r":\(\)\{.*\}",
]


class CommandFilter:
    """Classify shell commands into safe/elevated/forbidden."""

    def __init__(self) -> None:
        self._safe_patterns = self._compile(SAFE_PATTERNS)
        self._elevated_patterns = self._compile(ELEVATED_PATTERNS)
        self._forbidden_patterns = self._compile(FORBIDDEN_PATTERNS)

    @staticmethod
    def _compile(patterns: List[str]) -> List[Pattern[str]]:
        return [re.compile(p, re.IGNORECASE) for p in patterns]

    @staticmethod
    def _head_token(command: str) -> str:
        text = command.strip()
        if not text:
            return ""
        try:
            parts = shlex.split(text, posix=True)
        except ValueError:
            parts = text.split()
        if not parts:
            return ""

        idx = 0
        while idx < len(parts) and "=" in parts[idx] and not parts[idx].startswith(("./", "/")):
            left = parts[idx].split("=", 1)[0]
            if left and left.replace("_", "").isalnum() and not left[0].isdigit():
                idx += 1
                continue
            break
        if idx >= len(parts):
            return ""
        return parts[idx].strip().lower()

    @staticmethod
    def _match_pattern(command: str, patterns: List[Pattern[str]]) -> str | None:
        for pattern in patterns:
            if pattern.search(command):
                return pattern.pattern
        return None

    def classify(self, command: str) -> CommandDecision:
        text = str(command or "").strip()
        if not text:
            return CommandDecision(
                command=text,
                risk_level=RiskLevel.SAFE,
                reason="empty_command",
                requires_approval=False,
            )

        matched = self._match_pattern(text, self._forbidden_patterns)
        if matched:
            return CommandDecision(
                command=text,
                risk_level=RiskLevel.FORBIDDEN,
                reason=f"forbidden_pattern:{matched}",
                requires_approval=True,
            )

        head = self._head_token(text)
        if head in FORBIDDEN_COMMANDS:
            return CommandDecision(
                command=text,
                risk_level=RiskLevel.FORBIDDEN,
                reason=f"forbidden_command:{head}",
                requires_approval=True,
            )

        matched = self._match_pattern(text, self._elevated_patterns)
        if matched:
            return CommandDecision(
                command=text,
                risk_level=RiskLevel.ELEVATED,
                reason=f"elevated_pattern:{matched}",
                requires_approval=False,
            )

        if head in ELEVATED_COMMANDS:
            return CommandDecision(
                command=text,
                risk_level=RiskLevel.ELEVATED,
                reason=f"elevated_command:{head}",
                requires_approval=False,
            )

        matched = self._match_pattern(text, self._safe_patterns)
        if matched:
            return CommandDecision(
                command=text,
                risk_level=RiskLevel.SAFE,
                reason=f"safe_pattern:{matched}",
                requires_approval=False,
            )

        if head in SAFE_COMMANDS:
            return CommandDecision(
                command=text,
                risk_level=RiskLevel.SAFE,
                reason=f"safe_command:{head}",
                requires_approval=False,
            )

        # Conservative default for unknown commands.
        return CommandDecision(
            command=text,
            risk_level=RiskLevel.ELEVATED,
            reason="default_elevated",
            requires_approval=False,
        )
