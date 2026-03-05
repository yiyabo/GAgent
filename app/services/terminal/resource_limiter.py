"""Resource limit helpers for terminal subprocesses."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional

try:  # pragma: no cover - platform dependent
    import resource
except Exception:  # pragma: no cover
    resource = None  # type: ignore


@dataclass(frozen=True)
class ResourceLimits:
    cpu_seconds: int = 600
    memory_mb: int = 1024
    max_procs: int = 64


DEFAULT_TERMINAL_LIMITS = ResourceLimits()


def apply_limits_in_child(limits: Optional[ResourceLimits] = None) -> None:
    """Apply POSIX rlimits in a forked child process."""
    if resource is None:  # pragma: no cover
        return

    cfg = limits or DEFAULT_TERMINAL_LIMITS

    if hasattr(resource, "RLIMIT_CPU") and cfg.cpu_seconds > 0:
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (cfg.cpu_seconds, cfg.cpu_seconds))
        except (OSError, ValueError):  # pragma: no cover
            pass

    if hasattr(resource, "RLIMIT_AS") and cfg.memory_mb > 0:
        try:
            limit_bytes = int(cfg.memory_mb) * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
        except (OSError, ValueError):  # pragma: no cover
            pass

    # RLIMIT_NPROC on macOS is a per-user global limit (not per-process-tree).
    # Setting it to a small value immediately breaks fork() for ls, python, etc.
    # because the user already has hundreds of system processes.  Skip on macOS.
    if sys.platform != "darwin" and hasattr(resource, "RLIMIT_NPROC") and cfg.max_procs > 0:
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (cfg.max_procs, cfg.max_procs))
        except (OSError, ValueError):  # pragma: no cover
            pass
