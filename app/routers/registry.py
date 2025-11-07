from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional

from fastapi import APIRouter


@dataclass
class RouterEntry:
    """Metadata for an API router exposed to the frontend."""

    namespace: str
    version: str
    path: str
    router: APIRouter
    tags: List[str] = field(default_factory=list)
    requires_plan: bool = False
    allow_anonymous: bool = True
    deprecated: bool = False
    description: Optional[str] = None


class RouterRegistry:
    """Central registry for public API routers."""

    _entries: List[RouterEntry] = []

    @classmethod
    def register(cls, entry: RouterEntry) -> None:
        key = (entry.namespace, entry.version, entry.path)
        if any((e.namespace, e.version, e.path) == key for e in cls._entries):
            raise ValueError(f"Router {key} 已注册，请勿重复注册。")
        cls._entries.append(entry)

    @classmethod
    def entries(cls) -> List[RouterEntry]:
        return list(cls._entries)

    @classmethod
    def clear(cls) -> None:  # pragma: no cover - test helper
        cls._entries.clear()


def register_router(
    *,
    namespace: str,
    version: str,
    path: str,
    router: APIRouter,
    tags: Optional[Iterable[str]] = None,
    requires_plan: bool = False,
    allow_anonymous: bool = True,
    deprecated: bool = False,
    description: Optional[str] = None,
) -> None:
    """Helper to register router metadata."""

    RouterRegistry.register(
        RouterEntry(
            namespace=namespace,
            version=version,
            path=path,
            router=router,
            tags=list(tags or []),
            requires_plan=requires_plan,
            allow_anonymous=allow_anonymous,
            deprecated=deprecated,
            description=description,
        )
    )


def routers_for_fastapi() -> List[APIRouter]:
    """Return routers for FastAPI include_router."""
    return [entry.router for entry in RouterRegistry.entries()]


def generate_router_markdown() -> str:
    """Produce markdown table documenting registered routers."""
    if not RouterRegistry.entries():
        return "（当前未注册对外接口）"

    headers = [
        "| Namespace | Version | Prefix | Tags | Requires Plan | Deprecated | Description |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    rows = []
    for entry in RouterRegistry.entries():
        rows.append(
            "| {namespace} | {version} | `{path}` | {tags} | {plan} | {deprecated} | {desc} |".format(
                namespace=entry.namespace,
                version=entry.version,
                path=entry.path,
                tags=", ".join(entry.tags) or "-",
                plan="✅" if entry.requires_plan else "❌",
                deprecated="⚠️" if entry.deprecated else "❌",
                desc=entry.description or "-",
            )
        )
    return "\n".join(headers + rows)


__all__ = [
    "RouterRegistry",
    "RouterEntry",
    "register_router",
    "routers_for_fastapi",
    "generate_router_markdown",
]
