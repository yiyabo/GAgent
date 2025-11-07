from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional


@dataclass(frozen=True)
class ExecutorSettings:
    """Global defaults for PlanExecutor behaviour."""

    model: Optional[str] = None
    provider: Optional[str] = None
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    max_retries: int = 2
    timeout: Optional[float] = None
    serial: bool = True
    use_context: bool = True
    include_plan_outline: bool = True
    dependency_throttle: bool = True
    max_tasks: Optional[int] = None


@lru_cache(maxsize=1)
def get_executor_settings() -> ExecutorSettings:
    """Return cached executor settings derived from environment variables."""

    defaults = ExecutorSettings()

    def _env_bool(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    def _env_int(name: str, default: int) -> int:
        raw = os.getenv(name)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    def _env_float(name: str, default: Optional[float]) -> Optional[float]:
        raw = os.getenv(name)
        if raw is None:
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    max_tasks_raw = os.getenv("PLAN_EXECUTOR_MAX_TASKS")
    max_tasks = None
    if max_tasks_raw is not None:
        try:
            parsed = int(max_tasks_raw)
            if parsed > 0:
                max_tasks = parsed
        except ValueError:
            max_tasks = None

    return ExecutorSettings(
        model=os.getenv("PLAN_EXECUTOR_MODEL", defaults.model),
        provider=os.getenv("PLAN_EXECUTOR_PROVIDER", defaults.provider),
        api_url=os.getenv("PLAN_EXECUTOR_API_URL", defaults.api_url),
        api_key=os.getenv("PLAN_EXECUTOR_API_KEY", defaults.api_key),
        max_retries=max(
            1, _env_int("PLAN_EXECUTOR_MAX_RETRIES", defaults.max_retries)
        ),
        timeout=_env_float("PLAN_EXECUTOR_TIMEOUT", defaults.timeout),
        serial=_env_bool("PLAN_EXECUTOR_SERIAL", defaults.serial),
        use_context=_env_bool("PLAN_EXECUTOR_USE_CONTEXT", defaults.use_context),
        include_plan_outline=_env_bool(
            "PLAN_EXECUTOR_INCLUDE_OUTLINE", defaults.include_plan_outline
        ),
        dependency_throttle=_env_bool(
            "PLAN_EXECUTOR_DEP_THROTTLE", defaults.dependency_throttle
        ),
        max_tasks=max_tasks,
    )


__all__ = ["ExecutorSettings", "get_executor_settings"]
