"""Streamable HTTP MCP endpoint for PhageScope, mounted at exactly ``/mcp``.

Every request needs ``Authorization: Bearer gpsk_...``. The data-service key
stays on this server; callers only ever see tool results.
"""

from __future__ import annotations

import contextvars
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from mcp.server import Server, ServerRequestContext
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListResourcesResult,
    ListToolsResult,
    PaginatedRequestParams,
    ReadResourceRequestParams,
    ReadResourceResult,
    Resource,
    TextContent,
    TextResourceContents,
    Tool,
    ToolAnnotations,
)
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from app.services.foundation.settings import get_settings
from tool_box.tools_impl.phagescope_data.config import PhageScopeDataError

from . import store
from .limits import limiter
from .tools import (
    FEATURE_TYPES,
    FILTERS_SCHEMA,
    GROUP_BY_DIMENSIONS,
    TOOLS,
    TOOLS_BY_NAME,
    ToolInputError,
    build_envelope,
    fit_to_budget,
    snapshot_info,
)

logger = logging.getLogger(__name__)

SERVER_NAME = "phagescope"
MCP_PATH = "/mcp"
REFERENCE_URI = "phagescope://reference/query-guide"
_STATE_KEY = "phagescope_mcp_token"

INSTRUCTIONS = (
    "Read-only access to the PhageScope phage database (about 2.6M phage genomes, 131M proteins, "
    "12.6M protein families). Start with catalog_overview, use cohort_report/aggregate_phages for "
    "counts, search_phages for rows, then get_phage/get_sequence/search_proteins for detail. "
    "Missing values are unknown, not zero. Results are paged and size-capped; page instead of "
    "asking for everything."
)

_current_token: contextvars.ContextVar[Optional[store.McpToken]] = contextvars.ContextVar(
    "phagescope_mcp_token", default=None
)


def _token_for(ctx: ServerRequestContext) -> Optional[store.McpToken]:
    request = getattr(ctx, "request", None)
    scope = getattr(request, "scope", None)
    if isinstance(scope, dict):
        token = (scope.get("state") or {}).get(_STATE_KEY)
        if token is not None:
            return token
    return _current_token.get()


def _error(code: str, message: str) -> CallToolResult:
    payload = {"error": {"code": code, "message": message}}
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))], is_error=True)


async def _list_tools(ctx: ServerRequestContext, params: Optional[PaginatedRequestParams]) -> ListToolsResult:
    annotations = ToolAnnotations(
        read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
    )
    return ListToolsResult(
        tools=[
            Tool(name=t.name, title=t.title, description=t.description, input_schema=t.input_schema, annotations=annotations)
            for t in TOOLS
        ]
    )


async def _call_tool(ctx: ServerRequestContext, params: CallToolRequestParams) -> CallToolResult:
    token = _token_for(ctx)
    if token is None:
        return _error("unauthorized", "A valid PhageScope MCP token is required.")
    spec = TOOLS_BY_NAME.get(params.name)
    arguments = dict(params.arguments or {})
    if spec is None:
        return _error("unknown_tool", f"Unknown tool: {params.name}")

    settings = get_settings()
    daily = int(settings.phagescope_mcp_daily_bytes)
    started = time.monotonic()
    status, code, nbytes, rows = "error", None, 0, None
    try:
        used = store.bytes_used_today(token.id)
        if daily > 0 and used >= daily:
            code = "daily_quota_exceeded"
            return _error(code, f"Daily quota of {daily} bytes is used up; it resets at 00:00 UTC.")
        budget = int(settings.phagescope_mcp_max_result_bytes)
        if daily > 0:
            budget = max(1024, min(budget, daily - used))
        try:
            data, notes = await spec.run(arguments)
            envelope = build_envelope(spec.name, data, notes, await snapshot_info())
            text, nbytes = fit_to_budget(envelope, budget)
        except ToolInputError as exc:
            code = exc.code
            return _error(exc.code, str(exc))
        except PhageScopeDataError as exc:
            code = exc.code
            return _error(exc.code, exc.message)
        except Exception:  # the protocol needs an error result, not a crashed server
            logger.exception("PhageScope MCP tool %s failed", spec.name)
            code = "internal_error"
            return _error(code, f"{spec.name} failed unexpectedly.")
        rows = envelope["limits"].get("returned_rows")
        store.record_usage(token.id, nbytes)
        status = "ok"
        return CallToolResult(content=[TextContent(type="text", text=text)], is_error=False)
    finally:
        try:
            store.record_audit(
                token=token,
                tool=spec.name,
                arguments=arguments,
                status=status,
                error_code=code,
                duration_ms=int((time.monotonic() - started) * 1000),
                result_bytes=nbytes,
                result_rows=rows,
            )
        except Exception:
            logger.exception("PhageScope MCP audit write failed")


def _reference_text() -> str:
    return json.dumps(
        {
            "filters": FILTERS_SCHEMA["properties"],
            "group_by_dimensions": list(GROUP_BY_DIMENSIONS),
            "feature_types": list(FEATURE_TYPES),
            "semantics": [
                "Missing or null means unknown, never zero.",
                "Rows are paged; limits.truncated marks a size-trimmed result.",
                "Each token has a daily byte quota that resets at 00:00 UTC.",
            ],
        },
        ensure_ascii=False,
        indent=1,
    )


async def _list_resources(ctx: ServerRequestContext, params: Optional[PaginatedRequestParams]) -> ListResourcesResult:
    return ListResourcesResult(
        resources=[
            Resource(
                name="query-guide",
                title="PhageScope query guide",
                uri=REFERENCE_URI,
                description="Filter keys, aggregation dimensions, feature types and result semantics.",
                mime_type="application/json",
            )
        ]
    )


async def _read_resource(ctx: ServerRequestContext, params: ReadResourceRequestParams) -> ReadResourceResult:
    if str(params.uri) != REFERENCE_URI:
        raise ValueError(f"Unknown resource: {params.uri}")
    return ReadResourceResult(
        contents=[TextResourceContents(uri=REFERENCE_URI, mime_type="application/json", text=_reference_text())]
    )


def build_server() -> Server:
    return Server(
        SERVER_NAME,
        version="1",
        title="PhageScope",
        instructions=INSTRUCTIONS,
        on_list_tools=_list_tools,
        on_call_tool=_call_tool,
        on_list_resources=_list_resources,
        on_read_resource=_read_resource,
    )


def _bearer(scope: Scope) -> str:
    for name, value in scope.get("headers") or []:
        if name == b"authorization":
            text = value.decode("latin-1").strip()
            if text[:7].lower() == "bearer ":
                return text[7:].strip()
    return ""


def _refuse(status: int, code: str, message: str, extra_headers: Optional[dict[str, str]] = None) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if status == 401:
        headers["WWW-Authenticate"] = 'Bearer realm="phagescope-mcp"'
    headers.update(extra_headers or {})
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}}, headers=headers)


class PhageScopeMcpEndpoint:
    """ASGI endpoint: bearer auth, per-token limits, then the MCP transport."""

    def __init__(self) -> None:
        self._manager: Optional[StreamableHTTPSessionManager] = None

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        manager = StreamableHTTPSessionManager(
            app=build_server(),
            json_response=True,
            stateless=True,
            security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        async with manager.run():
            self._manager = manager
            try:
                yield
            finally:
                self._manager = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        settings = get_settings()
        if not settings.phagescope_mcp_enabled or self._manager is None:
            await _refuse(404, "not_enabled", "The PhageScope MCP endpoint is not enabled.")(scope, receive, send)
            return
        token = store.authenticate(_bearer(scope))
        if token is None:
            await _refuse(401, "unauthorized", "A valid PhageScope MCP bearer token is required.")(scope, receive, send)
            return
        if store.SCOPE_DATA_READ not in token.scopes:
            await _refuse(403, "insufficient_scope", "Token lacks the data:read scope.")(scope, receive, send)
            return
        refusal = limiter.acquire(
            token.id,
            per_minute=int(settings.phagescope_mcp_requests_per_minute),
            max_concurrency=int(settings.phagescope_mcp_max_concurrency),
        )
        if refusal is not None:
            await _refuse(429, refusal, "Too many requests for this token; retry shortly.", {"Retry-After": "10"})(
                scope, receive, send
            )
            return
        scope.setdefault("state", {})[_STATE_KEY] = token
        ctx_token = _current_token.set(token)
        try:
            await self._manager.handle_request(scope, receive, send)
        finally:
            _current_token.reset(ctx_token)
            limiter.release(token.id)


endpoint = PhageScopeMcpEndpoint()
