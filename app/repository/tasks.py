"""Legacy task repository placeholder.

The new dialogue workflow persists plans exclusively through
`app.repository.plan_repository.PlanRepository`.  Any attempt to use the
former task repository API should surface a clear runtime error so callers
can migrate to the new PlanTree-based implementation.
"""

from __future__ import annotations

from typing import Any


class LegacyTaskRepositoryError(RuntimeError):
    """Raised when legacy task repository code is accessed."""


class _DeprecatedProxy:
    """Attr-access proxy that always raises a migration error."""

    def __getattr__(self, item: str) -> Any:  # pragma: no cover - defensive
        raise LegacyTaskRepositoryError(
            "Legacy task repository APIs have been removed. "
            "Please migrate to `PlanRepository` and the PlanTree workflow."
        )

    def __call__(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - defensive
        raise LegacyTaskRepositoryError(
            "Legacy task repository APIs have been removed. "
            "Please migrate to `PlanRepository` and the PlanTree workflow."
        )


SqliteTaskRepository = _DeprecatedProxy  # type: ignore
default_repo = _DeprecatedProxy()

__all__ = ["SqliteTaskRepository", "default_repo", "LegacyTaskRepositoryError"]

