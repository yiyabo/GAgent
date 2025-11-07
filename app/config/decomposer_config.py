from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional


@dataclass(frozen=True)
class DecomposerSettings:
    """Configuration for PlanDecomposer behaviour."""

    max_depth: int = 2
    min_children: int = 2
    max_children: int = 5
    total_node_budget: int = 50
    model: Optional[str] = None
    provider: Optional[str] = None
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    auto_on_create: bool = True
    stop_on_empty: bool = True
    retry_limit: int = 1
    allow_existing_children: bool = False


@lru_cache(maxsize=1)
def get_decomposer_settings() -> DecomposerSettings:
    """Return cached settings instance.

    For now we rely on defaults; future work can read from environment or config files.
    """

    defaults = DecomposerSettings()

    def _env_int(name: str, default: int) -> int:
        raw = os.getenv(name)
        if raw is None:
            return default
        try:
            value = int(raw)
            return value
        except ValueError:
            return default

    def _env_bool(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    max_depth = _env_int("DECOMP_MAX_DEPTH", defaults.max_depth)
    min_children = _env_int("DECOMP_MIN_CHILDREN", defaults.min_children)
    max_children = _env_int("DECOMP_MAX_CHILDREN", defaults.max_children)
    if min_children < 1:
        min_children = 1
    if max_children < min_children:
        max_children = min_children

    settings = DecomposerSettings(
        max_depth=max_depth,
        min_children=min_children,
        max_children=max_children,
        total_node_budget=_env_int(
            "DECOMP_TOTAL_NODE_BUDGET", defaults.total_node_budget
        ),
        model=os.getenv("DECOMP_MODEL", defaults.model),
        provider=os.getenv("DECOMP_PROVIDER", defaults.provider),
        api_url=os.getenv("DECOMP_API_URL", defaults.api_url),
        api_key=os.getenv("DECOMP_API_KEY", defaults.api_key),
        auto_on_create=_env_bool("DECOMP_AUTO_ON_CREATE", defaults.auto_on_create),
        stop_on_empty=_env_bool("DECOMP_STOP_ON_EMPTY", defaults.stop_on_empty),
        retry_limit=_env_int("DECOMP_RETRY_LIMIT", defaults.retry_limit),
        allow_existing_children=_env_bool(
            "DECOMP_ALLOW_EXISTING_CHILDREN", defaults.allow_existing_children
        ),
    )

    return settings
