"""API router registration utilities."""

import importlib
from typing import Iterable

from .registry import RouterRegistry, register_router, routers_for_fastapi

_DEFAULT_MODULES: Iterable[str] = (
    "app.routers.chat_routes",
    "app.routers.system_health_routes",
    "app.routers.plan_routes",
    "app.routers.job_routes",
    "app.routers.execution_routes",
    "app.routers.upload_routes",
    "app.routers.artifact_routes",
)


def _ensure_default_routes_loaded() -> None:
    for module_name in _DEFAULT_MODULES:
        importlib.import_module(module_name)


_ensure_default_routes_loaded()


def get_all_routers():
    """Backwards compatibility: returns registered routers."""
    return routers_for_fastapi()


__all__ = [
    "RouterRegistry",
    "register_router",
    "routers_for_fastapi",
    "get_all_routers",
]
