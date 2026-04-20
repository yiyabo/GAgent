from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal, Tuple


RESEARCH_MODULES: Tuple[str, ...] = (
    "code",
    "docs",
    "image_tabular",
    "paper",
    "refs",
)


DeliverablesIngestMode = Literal["legacy", "explicit"]
DeliverableConflictStrategy = Literal["error", "rename", "keep_first"]


@dataclass(frozen=True)
class DeliverableSettings:
    enabled: bool = True
    default_template: str = "research"
    show_draft: bool = False
    history_max: int = 1
    single_version_only: bool = True
    modules: Tuple[str, ...] = RESEARCH_MODULES
    #: legacy: mirror paths from tool results heuristically; explicit: only manifest + deliverable_submit + manuscript tools
    ingest_mode: DeliverablesIngestMode = "explicit"
    #: basename collision policy when different source files target the same deliverable name
    basename_conflict_strategy: DeliverableConflictStrategy = "error"


@lru_cache(maxsize=1)
def get_deliverable_settings() -> DeliverableSettings:
    defaults = DeliverableSettings()

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

    template = (os.getenv("DELIVERABLES_DEFAULT_TEMPLATE", defaults.default_template) or defaults.default_template).strip().lower()
    if template != "research":
        template = "research"

    single_version_only = _env_bool(
        "DELIVERABLES_SINGLE_VERSION_ONLY",
        defaults.single_version_only,
    )
    history_max = 1 if single_version_only else max(1, _env_int("DELIVERABLES_HISTORY_MAX", defaults.history_max))

    raw_ingest = (os.getenv("DELIVERABLES_INGEST_MODE", defaults.ingest_mode) or "explicit").strip().lower()
    if raw_ingest not in {"legacy", "explicit"}:
        raw_ingest = "explicit"
    ingest_mode: DeliverablesIngestMode = raw_ingest  # type: ignore[assignment]

    raw_conflict_strategy = (
        os.getenv(
            "DELIVERABLES_BASENAME_CONFLICT_STRATEGY",
            defaults.basename_conflict_strategy,
        )
        or defaults.basename_conflict_strategy
    ).strip().lower()
    if raw_conflict_strategy not in {"error", "rename", "keep_first"}:
        raw_conflict_strategy = defaults.basename_conflict_strategy
    basename_conflict_strategy: DeliverableConflictStrategy = raw_conflict_strategy  # type: ignore[assignment]

    return DeliverableSettings(
        enabled=_env_bool("DELIVERABLES_ENABLED", defaults.enabled),
        default_template=template,
        show_draft=_env_bool("DELIVERABLES_SHOW_DRAFT", defaults.show_draft),
        history_max=history_max,
        single_version_only=single_version_only,
        modules=defaults.modules,
        ingest_mode=ingest_mode,
        basename_conflict_strategy=basename_conflict_strategy,
    )


__all__ = [
    "DeliverableConflictStrategy",
    "DeliverableSettings",
    "DeliverablesIngestMode",
    "RESEARCH_MODULES",
    "get_deliverable_settings",
]
