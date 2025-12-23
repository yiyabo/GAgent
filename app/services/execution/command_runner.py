"""Utilities for executing shell commands in controlled subprocesses."""

from __future__ import annotations

import asyncio
import os
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, MutableMapping, Optional, Sequence

try:  # pragma: no cover - platforms without ``resource``
    import resource
except ImportError:  # pragma: no cover
    resource = None  # type: ignore

_DEFAULT_TIMEOUT = int(os.getenv("EXECUTION_DEFAULT_TIMEOUT", "60"))
_MAX_OUTPUT_BYTES = int(os.getenv("EXECUTION_MAX_OUTPUT_BYTES", str(512 * 1024)))
_MEMORY_LIMIT_MB = int(os.getenv("EXECUTION_MEMORY_LIMIT_MB", "512"))
_CPU_TIME_LIMIT_SEC = int(os.getenv("EXECUTION_CPU_LIMIT_SEC", "30"))
_BLACKLISTED_COMMANDS = {
    "rm",
    "shutdown",
    "reboot",
    "halt",
    "poweroff",
}


@dataclass
class CommandResult:
    command: Sequence[str]
    exit_code: Optional[int]
    stdout: str
    stderr: str
    duration: float
    timed_out: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False

    def to_dict(self) -> dict:
        return {
            "command": list(self.command),
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration": self.duration,
            "timed_out": self.timed_out,
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
        }


def _validate_command(args: Sequence[str]) -> None:
    if not args:
        raise ValueError("Command arguments cannot be empty")
    head = args[0]
    if head in _BLACKLISTED_COMMANDS:
        raise ValueError(f"Command '{head}' is not permitted")


def _truncate_output(data: bytes) -> tuple[str, bool]:
    if _MAX_OUTPUT_BYTES <= 0:
        return data.decode("utf-8", errors="replace"), False
    if len(data) <= _MAX_OUTPUT_BYTES:
        return data.decode("utf-8", errors="replace"), False
    truncated = data[: _MAX_OUTPUT_BYTES]
    return truncated.decode("utf-8", errors="replace"), True


def _build_preexec_fn():  # pragma: no cover - not testable on non-POSIX
    if resource is None:
        return None

    has_cpu_limit = hasattr(resource, "RLIMIT_CPU")
    has_address_space_limit = hasattr(resource, "RLIMIT_AS")

    if not (has_cpu_limit or has_address_space_limit):
        return None

    def limiter() -> None:
        if has_cpu_limit and _CPU_TIME_LIMIT_SEC > 0:
            try:
                resource.setrlimit(resource.RLIMIT_CPU, (_CPU_TIME_LIMIT_SEC, _CPU_TIME_LIMIT_SEC))
            except (ValueError, OSError):  # pragma: no cover - platform specific
                pass
        if has_address_space_limit and _MEMORY_LIMIT_MB > 0:
            bytes_limit = _MEMORY_LIMIT_MB * 1024 * 1024
            try:
                resource.setrlimit(resource.RLIMIT_AS, (bytes_limit, bytes_limit))
            except (ValueError, OSError):  # pragma: no cover - platform specific
                pass

    return limiter


async def run_shell_command(
    args: Sequence[str],
    *,
    cwd: Path,
    timeout: Optional[int] = None,
    env: Optional[Mapping[str, str]] = None,
) -> CommandResult:
    """Execute *args* inside *cwd* and return structured result."""
    _validate_command(args)
    timeout = timeout or _DEFAULT_TIMEOUT
    cwd_path = cwd.resolve()
    if not cwd_path.is_dir():
        raise ValueError(f"Working directory does not exist: {cwd_path}")

    merged_env: MutableMapping[str, str] = os.environ.copy()
    if env:
        merged_env.update(env)

    preexec_fn = _build_preexec_fn()

    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(cwd_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=dict(merged_env),
            preexec_fn=preexec_fn,  # type: ignore[arg-type]
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            timed_out = False
        except asyncio.TimeoutError:
            proc.kill()
            stdout_bytes, stderr_bytes = await proc.communicate()
            timed_out = True
    except FileNotFoundError as exc:
        raise ValueError(f"Failed to execute command: {exc}")

    duration = time.monotonic() - start
    stdout_text, stdout_truncated = _truncate_output(stdout_bytes)
    stderr_text, stderr_truncated = _truncate_output(stderr_bytes)

    return CommandResult(
        command=args,
        exit_code=proc.returncode,
        stdout=stdout_text,
        stderr=stderr_text,
        duration=duration,
        timed_out=timed_out,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
    )


def parse_command(command: str | Sequence[str]) -> Sequence[str]:
    """Convert string command into argv sequence with shlex splitting."""
    if isinstance(command, str):
        parts = shlex.split(command, posix=True)
        if not parts:
            raise ValueError("Command string cannot be empty")
        return parts
    return list(command)
