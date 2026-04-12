from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from app.services.foundation.settings import get_settings

DEFAULT_CODE_EXECUTION_LOCAL_RUNTIME = "docker"
DEFAULT_CODE_EXECUTION_DOCKER_IMAGE = "gagent-python-runtime:latest"


def resolve_code_execution_local_runtime(
    value: Optional[str],
    *,
    default: str = DEFAULT_CODE_EXECUTION_LOCAL_RUNTIME,
) -> str:
    raw = str(value or "").strip().lower()
    if raw == "local":
        return "host"
    if raw in {"docker", "host"}:
        return raw
    return default


def resolve_code_execution_docker_image(
    value: Optional[str],
    *,
    default: str = DEFAULT_CODE_EXECUTION_DOCKER_IMAGE,
) -> str:
    raw = str(value or "").strip()
    return raw or default


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
    enable_skills: bool = True
    skill_budget_chars: int = 6000
    skill_selection_mode: str = "hybrid"
    skill_max_per_task: int = 3
    skill_trace_enabled: bool = True
    code_execution_backend: str = "auto"  # "auto" | "local" | "qwen_code" | "claude_code"
    code_execution_auto_strategy: str = "qwen_primary"  # "qwen_primary" | "split"
    code_execution_local_runtime: str = DEFAULT_CODE_EXECUTION_LOCAL_RUNTIME
    code_execution_docker_image: str = DEFAULT_CODE_EXECUTION_DOCKER_IMAGE
    # --- Layer 4: configurable limits ---
    deep_think_max_iterations: int = 12
    qc_max_session_turns: int = 50
    qc_shell_timeout_ms: int = 600000
    code_execution_timeout: int = 120
    # --- Layer 1/3: auto-execution and recovery ---
    force_rerun: bool = False
    auto_recovery: bool = False
    max_recovery_attempts: int = 2
    autonomous: bool = False


@lru_cache(maxsize=1)
def get_executor_settings() -> ExecutorSettings:
    """Return cached executor settings derived from environment variables."""

    app_settings = get_settings()
    defaults = ExecutorSettings(
        enable_skills=bool(getattr(app_settings, "enable_skills", True)),
        skill_budget_chars=int(getattr(app_settings, "skill_budget_chars", 6000)),
        skill_selection_mode=str(
            getattr(app_settings, "skill_selection_mode", "hybrid")
        ),
        skill_max_per_task=int(getattr(app_settings, "skill_max_per_task", 3)),
        skill_trace_enabled=bool(
            getattr(app_settings, "skill_trace_enabled", True)
        ),
    )

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

    def _env_choice(name: str, default: str, choices: set[str]) -> str:
        raw = os.getenv(name)
        if raw is None:
            return default
        value = str(raw).strip().lower()
        if value in choices:
            return value
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
        enable_skills=_env_bool("ENABLE_SKILLS", defaults.enable_skills),
        skill_budget_chars=max(
            1, _env_int("SKILL_BUDGET_CHARS", defaults.skill_budget_chars)
        ),
        skill_selection_mode=_env_choice(
            "SKILL_SELECTION_MODE",
            defaults.skill_selection_mode,
            {"hybrid", "llm_only"},
        ),
        skill_max_per_task=max(
            1, _env_int("SKILL_MAX_PER_TASK", defaults.skill_max_per_task)
        ),
        skill_trace_enabled=_env_bool(
            "SKILL_TRACE_ENABLED", defaults.skill_trace_enabled
        ),
        code_execution_backend=_env_choice(
            "CODE_EXECUTION_BACKEND",
            defaults.code_execution_backend,
            {"auto", "local", "qwen_code", "claude_code"},
        ),
        code_execution_auto_strategy=_env_choice(
            "CODE_EXECUTION_AUTO_STRATEGY",
            defaults.code_execution_auto_strategy,
            {"qwen_primary", "split"},
        ),
        code_execution_local_runtime=resolve_code_execution_local_runtime(
            os.getenv("CODE_EXECUTOR_LOCAL_RUNTIME"),
            default=defaults.code_execution_local_runtime,
        ),
        code_execution_docker_image=resolve_code_execution_docker_image(
            os.getenv("CODE_EXECUTOR_DOCKER_IMAGE"),
            default=defaults.code_execution_docker_image,
        ),
        deep_think_max_iterations=max(
            1, min(50, _env_int("DEEP_THINK_MAX_ITERATIONS", defaults.deep_think_max_iterations))
        ),
        qc_max_session_turns=max(
            10, min(500, _env_int("QC_MAX_SESSION_TURNS", defaults.qc_max_session_turns))
        ),
        qc_shell_timeout_ms=max(
            1000, min(600000, _env_int("QC_SHELL_TIMEOUT_MS", defaults.qc_shell_timeout_ms))
        ),
        code_execution_timeout=max(
            30, min(3600, _env_int("CODE_EXECUTION_TIMEOUT", defaults.code_execution_timeout))
        ),
        force_rerun=_env_bool("PLAN_EXECUTOR_FORCE_RERUN", defaults.force_rerun),
        auto_recovery=_env_bool("PLAN_EXECUTOR_AUTO_RECOVERY", defaults.auto_recovery),
        max_recovery_attempts=max(
            1, min(5, _env_int("PLAN_EXECUTOR_MAX_RECOVERY", defaults.max_recovery_attempts))
        ),
        autonomous=_env_bool("PLAN_EXECUTOR_AUTONOMOUS", defaults.autonomous),
    )

__all__ = [
    "DEFAULT_CODE_EXECUTION_DOCKER_IMAGE",
    "DEFAULT_CODE_EXECUTION_LOCAL_RUNTIME",
    "ExecutorSettings",
    "get_executor_settings",
    "resolve_code_execution_docker_image",
    "resolve_code_execution_local_runtime",
]
