"""Streamable HTTP MCP endpoint (PhageAgent MCP, PhageScope data), mounted at exactly ``/mcp``.

Tool calls need ``Authorization: Bearer gpsk_...``; a 401 says where to get
one. With ``PHAGESCOPE_MCP_ANON_DISCOVERY`` on, requests without a token may only
initialize, ping and list tools, so directories can see the server; everything
else still answers 401. Requests without a valid token are rate-limited per
client IP, and every request also counts against a per-IP limit. Argument errors
always use the code ``invalid_arguments``. The data-service key stays on this
server; callers only ever see tool results.
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

from . import analysis, local_tools, store
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

SERVER_NAME = "phageagent"
MCP_PATH = "/mcp"
REFERENCE_URI = "phageagent://reference/query-guide"
_STATE_KEY = "phagescope_mcp_token"

INSTRUCTIONS = (
    "PhageAgent MCP: read-only access to the PhageScope phage database (about 2.6M phage genomes, 131M proteins, "
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


INVALID_ARGUMENTS = "invalid_arguments"
_ARGUMENT_ERROR_ALIASES = frozenset({"invalid_argument", "invalid_parameter"})


def _public_code(code: str) -> str:
    """One code for bad arguments, whichever layer (MCP tools, analysis, data service) raised it."""
    return INVALID_ARGUMENTS if code in _ARGUMENT_ERROR_ALIASES else code


def _token_help() -> tuple[str, str]:
    url = str(get_settings().phagescope_mcp_token_help_url or "").strip()
    hint = "Send 'Authorization: Bearer gpsk_...'; create a token under Extensions > External access after signing in"
    return (f"{hint}: {url}" if url else f"{hint}."), url


def _error(code: str, message: str, **extra: Any) -> CallToolResult:
    payload = {"error": {"code": _public_code(code), "message": message, **extra}}
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))], is_error=True)


def _analysis_allowed(token: Optional[store.McpToken]) -> bool:
    return token is not None and analysis.enabled() and store.SCOPE_ANALYSIS_SUBMIT in token.scopes


async def _list_tools(ctx: ServerRequestContext, params: Optional[PaginatedRequestParams]) -> ListToolsResult:
    token = _token_for(ctx)
    annotations = ToolAnnotations(
        read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
    )
    anonymous = token is None
    tools: list[Tool] = []
    if anonymous or store.SCOPE_DATA_READ in token.scopes:
        tools += [
            Tool(name=t.name, title=t.title, description=t.description, input_schema=t.input_schema, annotations=annotations)
            for t in (*TOOLS, *local_tools.LOCAL_TOOLS)
        ]
    settings = get_settings()
    show_analysis = _analysis_allowed(token) or (
        anonymous and analysis.enabled() and settings.phagescope_mcp_open_access
    )
    if show_analysis:
        for spec in analysis.ANALYSIS_TOOLS:
            hints = ToolAnnotations(
                read_only_hint=spec.read_only,
                destructive_hint=False,
                idempotent_hint=spec.read_only,
                open_world_hint=False,
            )
            tools.append(
                Tool(name=spec.name, title=spec.title, description=spec.description, input_schema=spec.input_schema, annotations=hints)
            )
    return ListToolsResult(tools=tools)


async def _call_analysis_tool(token: store.McpToken, name: str, arguments: dict[str, Any]) -> CallToolResult:
    spec = analysis.ANALYSIS_TOOLS_BY_NAME[name]
    started = time.monotonic()
    status, code, nbytes = "error", None, 0
    try:
        if not _analysis_allowed(token):
            code = "insufficient_scope"
            return _error(code, "This token cannot submit analysis tasks.")
        try:
            data = await spec.run(token, arguments)
        except analysis.AnalysisError as exc:
            code = _public_code(exc.code)
            return _error(code, str(exc))
        except Exception:
            logger.exception("PhageScope MCP analysis tool %s failed", name)
            code = "internal_error"
            return _error(code, f"{name} failed unexpectedly.")
        text = json.dumps(data, ensure_ascii=False, default=str)
        nbytes = len(text.encode("utf-8"))
        status = "ok"
        return CallToolResult(content=[TextContent(type="text", text=text)], is_error=False)
    finally:
        try:
            store.record_audit(
                token=token,
                tool=name,
                arguments={k: v for k, v in arguments.items() if k != "inline_data"},
                status=status,
                error_code=code,
                duration_ms=int((time.monotonic() - started) * 1000),
                result_bytes=nbytes,
            )
        except Exception:
            logger.exception("PhageScope MCP audit write failed")


async def _call_local_tool(token: store.McpToken, name: str, arguments: dict[str, Any]) -> CallToolResult:
    spec = local_tools.LOCAL_TOOLS_BY_NAME[name]
    started = time.monotonic()
    status, code, nbytes = "error", None, 0
    try:
        if store.SCOPE_DATA_READ not in token.scopes:
            code = "insufficient_scope"
            return _error(code, "Token lacks the data:read scope.")
        try:
            data = await spec.run(arguments)
        except ToolInputError as exc:
            code = _public_code(exc.code)
            return _error(code, str(exc))
        except Exception:
            logger.exception("PhageScope MCP local tool %s failed", name)
            code = "internal_error"
            return _error(code, f"{name} failed unexpectedly.")
        text = json.dumps(
            {"tool": name, "source": local_tools.SOURCE, "data": data}, ensure_ascii=False, default=str
        )
        nbytes = len(text.encode("utf-8"))
        status = "ok"
        return CallToolResult(content=[TextContent(type="text", text=text)], is_error=False)
    finally:
        try:
            store.record_audit(
                token=token,
                tool=name,
                arguments=arguments,
                status=status,
                error_code=code,
                duration_ms=int((time.monotonic() - started) * 1000),
                result_bytes=nbytes,
            )
        except Exception:
            logger.exception("PhageScope MCP audit write failed")


async def _call_tool(ctx: ServerRequestContext, params: CallToolRequestParams) -> CallToolResult:
    token = _token_for(ctx)
    if token is None:
        hint, url = _token_help()
        return _error("unauthorized", f"A valid PhageAgent MCP token is required. {hint}", **({"help_url": url} if url else {}))
    spec = TOOLS_BY_NAME.get(params.name)
    arguments = dict(params.arguments or {})
    if spec is None and params.name in analysis.ANALYSIS_TOOLS_BY_NAME:
        return await _call_analysis_tool(token, params.name, arguments)
    if spec is None and params.name in local_tools.LOCAL_TOOLS_BY_NAME:
        return await _call_local_tool(token, params.name, arguments)
    if spec is None:
        return _error("unknown_tool", f"Unknown tool: {params.name}")
    if store.SCOPE_DATA_READ not in token.scopes:
        return _error("insufficient_scope", "Token lacks the data:read scope.")

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
            code = _public_code(exc.code)
            return _error(code, str(exc))
        except PhageScopeDataError as exc:
            code = _public_code(exc.code)
            return _error(code, exc.message)
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
        title="PhageAgent",
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


ANONYMOUS_METHODS = frozenset({"initialize", "notifications/initialized", "ping", "tools/list"})
_MAX_ANONYMOUS_BODY = 64 * 1024


def _client_ip(scope: Scope) -> str:
    for name, value in scope.get("headers") or []:
        if name == b"x-real-ip":
            text = value.decode("latin-1").strip()
            if text:
                return text
    client = scope.get("client")
    return str(client[0]) if client else "unknown"


async def _read_body(receive: Receive, limit: int) -> Optional[bytes]:
    """Whole request body, or ``None`` when it is larger than ``limit``."""
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            return None
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)


def _rpc_methods(body: bytes) -> Optional[set[str]]:
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    messages = payload if isinstance(payload, list) else [payload]
    methods: set[str] = set()
    for item in messages:
        if not isinstance(item, dict) or not isinstance(item.get("method"), str):
            return None
        methods.add(item["method"])
    return methods or None


def _csv_setting(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _refuse(status: int, code: str, message: str, extra_headers: Optional[dict[str, str]] = None) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    error: dict[str, Any] = {"code": code, "message": message}
    if status == 401:
        headers["WWW-Authenticate"] = 'Bearer realm="phagescope-mcp"'
        hint, url = _token_help()
        error["message"] = f"{message} {hint}"
        if url:
            error["help_url"] = url
    headers.update(extra_headers or {})
    return JSONResponse(status_code=status, content={"error": error}, headers=headers)


def _too_many(code: str, message: str) -> JSONResponse:
    return _refuse(429, code, message, {"Retry-After": "10"})


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
            security_settings=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=_csv_setting(get_settings().phagescope_mcp_allowed_hosts),
                allowed_origins=_csv_setting(get_settings().phagescope_mcp_allowed_origins),
            ),
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
            await _refuse(404, "not_enabled", "The PhageAgent MCP endpoint is not enabled.")(scope, receive, send)
            return
        ip = _client_ip(scope)
        ip_key = f"ip:{ip}"
        if limiter.acquire(ip_key, per_minute=int(settings.phagescope_mcp_ip_requests_per_minute), max_concurrency=0):
            await _too_many("rate_limited", "Too many requests from this address; retry shortly.")(scope, receive, send)
            return
        try:
            await self._dispatch(scope, receive, send, ip)
        finally:
            limiter.release(ip_key)

    async def _dispatch(self, scope: Scope, receive: Receive, send: Send, ip: str) -> None:
        settings = get_settings()
        presented = _bearer(scope)
        token = store.authenticate(presented) if presented else None
        if token is None:
            await self._without_token(scope, receive, send, ip, anonymous_ok=not presented)
            return
        if not set(token.scopes) & set(store.KNOWN_SCOPES):
            await _refuse(403, "insufficient_scope", "Token has no PhageAgent MCP scope.")(scope, receive, send)
            return
        refusal = limiter.acquire(
            token.id,
            per_minute=int(settings.phagescope_mcp_requests_per_minute),
            max_concurrency=int(settings.phagescope_mcp_max_concurrency),
        )
        if refusal is not None:
            await _too_many(refusal, "Too many requests for this token; retry shortly.")(scope, receive, send)
            return
        scope.setdefault("state", {})[_STATE_KEY] = token
        ctx_token = _current_token.set(token)
        try:
            await self._manager.handle_request(scope, receive, send)
        finally:
            _current_token.reset(ctx_token)
            limiter.release(token.id)

    async def _without_token(self, scope: Scope, receive: Receive, send: Send, ip: str, *, anonymous_ok: bool) -> None:
        """Missing or invalid token: per-IP limited, then anonymous discovery or 401."""
        settings = get_settings()
        key = f"anon:{ip}"
        refusal = limiter.acquire(
            key,
            per_minute=int(settings.phagescope_mcp_anon_requests_per_minute),
            max_concurrency=int(settings.phagescope_mcp_max_concurrency),
        )
        if refusal is not None:
            await _too_many(refusal, "Too many requests without a valid token; retry shortly.")(scope, receive, send)
            return
        try:
            if anonymous_ok and settings.phagescope_mcp_anon_discovery and scope.get("method") == "POST":
                await self._anonymous(scope, receive, send)
                return
            await _refuse(401, "unauthorized", "A valid PhageAgent MCP bearer token is required.")(scope, receive, send)
        finally:
            limiter.release(key)

    async def _anonymous(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Initialize, ping and tools/list without a token; anything else is 401."""
        body = await _read_body(receive, _MAX_ANONYMOUS_BODY)
        methods = _rpc_methods(body) if body is not None else None
        if not methods or not methods <= ANONYMOUS_METHODS:
            await _refuse(401, "unauthorized", "A valid PhageAgent MCP bearer token is required.")(scope, receive, send)
            return
        replayed = False

        async def replay() -> dict[str, Any]:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self._manager.handle_request(scope, replay, send)


endpoint = PhageScopeMcpEndpoint()
