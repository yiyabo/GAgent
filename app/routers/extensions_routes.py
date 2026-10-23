"""Extensions API: vetted MCP servers and skills, and who may switch them.

Reading is open to every authenticated user. Switching is admin-only, and only
catalog entries can be switched: the API never accepts a command, image or URL.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from ..services.auth import legacy_proxy_access_allowed
from ..services.extensions import service as mcp_service
from ..services.extensions import skills_view
from ..services.extensions.service import ExtensionError
from ..services.request_principal import (
    _quality_analytics_admin_ids,
    get_request_principal,
    require_authenticated_principal,
)
from ..services.skills.skills_loader import configured_runtime_skills_dir
from . import register_router

router = APIRouter(prefix="/extensions", tags=["extensions"])

register_router(
    namespace="extensions",
    version="v1",
    path="/extensions",
    router=router,
    tags=["extensions"],
    description="Vetted MCP servers and skills: list, verification evidence, enable/disable",
)


def _require_reader(request: Request) -> None:
    principal = get_request_principal(request)
    if not principal.is_authenticated and legacy_proxy_access_allowed(principal):
        return  # single-tenant legacy deployment: no ownership model, same as /usage
    require_authenticated_principal(request)


def _is_admin(request: Request) -> bool:
    principal = get_request_principal(request)
    if not principal.is_authenticated:
        return False  # legacy anonymous access can read but never switch
    return principal.role == "admin" or principal.user_id in _quality_analytics_admin_ids()


def _require_admin(request: Request) -> None:
    _require_reader(request)
    if not _is_admin(request):
        raise HTTPException(status_code=403, detail="只有管理员可以启用或停用扩展")


def _raise(exc: ExtensionError) -> None:
    raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


@router.get("/mcp")
async def list_mcp(request: Request) -> Dict[str, Any]:
    _require_reader(request)
    return await mcp_service.list_mcp(can_manage=_is_admin(request))


@router.post("/mcp/{server_id}/enable")
async def enable_mcp(server_id: str, request: Request) -> Dict[str, Any]:
    _require_admin(request)
    try:
        return await mcp_service.set_mcp_enabled(server_id, True)
    except ExtensionError as exc:
        _raise(exc)


@router.post("/mcp/{server_id}/disable")
async def disable_mcp(server_id: str, request: Request) -> Dict[str, Any]:
    _require_admin(request)
    try:
        return await mcp_service.set_mcp_enabled(server_id, False)
    except ExtensionError as exc:
        _raise(exc)


@router.get("/skills")
async def list_skills(request: Request) -> Dict[str, Any]:
    _require_reader(request)
    return await asyncio.to_thread(skills_view.list_skills, can_manage=_is_admin(request), extra_dir=configured_runtime_skills_dir())


@router.post("/skills/{name}/enable")
async def enable_skill(name: str, request: Request) -> Dict[str, Any]:
    _require_admin(request)
    try:
        return await asyncio.to_thread(skills_view.set_skill_enabled, name, True, extra_dir=configured_runtime_skills_dir())
    except ExtensionError as exc:
        _raise(exc)


@router.post("/skills/{name}/disable")
async def disable_skill(name: str, request: Request) -> Dict[str, Any]:
    _require_admin(request)
    try:
        return await asyncio.to_thread(skills_view.set_skill_enabled, name, False, extra_dir=configured_runtime_skills_dir())
    except ExtensionError as exc:
        _raise(exc)
