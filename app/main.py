"""FastAPI main application module for AI-Driven Task Orchestration System.

This module provides the main FastAPI application entry point with centralized
configuration, lifecycle management, and route registration.
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from tool_box import initialize_toolbox

# Ensure memory API routes are registered
from .api import memory_api  # noqa: F401
from .database import init_db
from .database_pool import get_db
from .errors import (
    BaseError,
    BusinessError,
    ErrorCode,
    ValidationError,
    handle_api_error,
)
from .errors.exceptions import ErrorCategory
from .errors.exceptions import SystemError as CustomSystemError
from .llm import get_default_client, init_shared_clients, close_shared_clients
from .middleware.proxy_auth import ProxyAuthMiddleware
from .services.realtime_bus import close_realtime_bus, init_realtime_bus

# Import router function
from .routers import get_all_routers
from .repository.plan_storage import fix_stale_jobs_on_startup
from .services.foundation.logging_config import setup_logging
from .services.foundation.settings import get_settings
from .utils.route_helpers import parse_bool


@asynccontextmanager
async def lifespan(_fastapi_app: FastAPI):
    """Application lifespan context manager for FastAPI startup and shutdown.

    Handles initialization of core components including logging, database,
    database integrity checks, and tool box integration during startup.
    Provides cleanup during shutdown.

    Args:
        _fastapi_app: FastAPI application instance (unused parameter required by FastAPI)

    Yields:
        None
    """
    # Initialize Structured Logging with Global Configuration
    setup_logging()
    _ = get_settings()  # Trigger loading to make it easy to see in the logs if the configuration took effect or not
    init_db()

    # Pre-warm shared HTTP connection pools for LLM API communication.
    # This eliminates per-request TCP/TLS handshake overhead (~60-150 ms each).
    await init_shared_clients()
    await init_realtime_bus()
    # DB Lightweight integrity check (logging only, no service interruption)
    try:
        with get_db() as _conn:
            row = _conn.execute("PRAGMA integrity_check").fetchone()
            msg = None
            try:
                msg = row[0]
            except (ValueError, TypeError):
                msg = str(row)
            logging.getLogger("app.main").info("DB integrity_check: %s", msg)
    except (ValueError, TypeError) as _e:
        logging.getLogger("app.main").warning("DB integrity check skipped: %s", _e)

    # Initialize Tool Box for enhanced agent capabilities
    try:
        await initialize_toolbox()
        logging.getLogger("app.main").info(
            "Tool Box integrated successfully - Enhanced AI capabilities enabled"
        )
    except Exception as e:
        logging.getLogger("app.main").warning("Tool Box initialization failed: %s", e)

    # Fix any stale jobs from previous server runs
    try:
        fixed_count = fix_stale_jobs_on_startup()
        if fixed_count > 0:
            logging.getLogger("app.main").info(
                "Fixed %d stale jobs from previous server run", fixed_count
            )
    except Exception as e:
        logging.getLogger("app.main").warning("Failed to fix stale jobs: %s", e)

    # Resume any PhageScope tracking threads that were running before restart
    try:
        from app.repository.plan_storage import get_running_phagescope_trackings
        from app.services.plans.decomposition_jobs import (
            plan_decomposition_jobs as _pdjobs,
            start_phagescope_track_job_thread,
        )
        tracking_rows = get_running_phagescope_trackings()
        resumed = 0
        for row in tracking_rows:
            job_id = row["job_id"]
            # Re-create the in-memory job so the board can find it
            try:
                _pdjobs.create_job(
                    plan_id=row.get("plan_id"),
                    task_id=None,
                    mode="phagescope_track",
                    job_type="phagescope_track",
                    params={"session_id": row.get("session_id"), "mode": "phagescope_track"},
                    metadata={"session_id": row.get("session_id"), "resumed_on_startup": True},
                    job_id=job_id,
                )
            except ValueError:
                pass  # job already exists in memory
            start_phagescope_track_job_thread(
                job_id=job_id,
                remote_taskid=row["remote_taskid"],
                modulelist=row.get("modulelist") or [],
                poll_interval=float(row.get("poll_interval") or 30.0),
                poll_timeout=float(row.get("poll_timeout") or 172800.0),
            )
            resumed += 1
        if resumed > 0:
            logging.getLogger("app.main").info(
                "Resumed %d PhageScope tracking thread(s) from previous run", resumed
            )
    except Exception as e:
        logging.getLogger("app.main").warning("Failed to resume PhageScope tracking: %s", e)

    yield

    # Gracefully close shared HTTP connection pools on shutdown.
    await close_realtime_bus()
    await close_shared_clients()


async def base_error_handler(_request: Request, exc: BaseError):
    """exception."""
    error_response = handle_api_error(exc, include_debug=False)
    return JSONResponse(
        status_code=_map_error_to_http_status(exc), content=error_response
    )


async def validation_exception_handler(_request: Request, exc: RequestValidationError):
    """Handle FastAPI request validation errors."""
    validation_error = ValidationError(
        message="Request parameter validation failed",
        error_code=ErrorCode.SCHEMA_VALIDATION_FAILED,
        context={"errors": exc.errors(), "body": str(exc.body) if exc.body else None},
        suggestions=["Check request parameters", "Review API parameter docs", "Submit valid payload"],
    )
    error_response = handle_api_error(validation_error, include_debug=False)
    return JSONResponse(status_code=422, content=error_response)


async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Handle Starlette HTTP exceptions."""
    if exc.status_code == 404:
        error = BusinessError(
            message="Requested resource not found",
            error_code=ErrorCode.TASK_NOT_FOUND,
            context={"path": str(request.url), "method": request.method},
        )
    elif exc.status_code == 405:
        error = ValidationError(
            message="HTTP method not allowed",
            error_code=ErrorCode.INVALID_FIELD_FORMAT,
            context={"method": request.method, "path": str(request.url)},
        )
    else:
        error = CustomSystemError(
            message=exc.detail if exc.detail else f"HTTP error {exc.status_code}",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            context={"status_code": exc.status_code},
        )

    error_response = handle_api_error(error, include_debug=False)
    return JSONResponse(status_code=exc.status_code, content=error_response)


async def general_exception_handler(request: Request, exc: Exception):
    """Handle uncaught application exceptions."""
    system_error = CustomSystemError(
        message="Internal server error",
        error_code=ErrorCode.INTERNAL_SERVER_ERROR,
        cause=exc,
        context={
            "path": str(request.url),
            "method": request.method,
            "exception_type": type(exc).__name__,
        },
        suggestions=["Retry the request", "If persistent, contact support with request details"],
    )
    debug_env = (
        os.environ.get("API_DEBUG")
        or os.environ.get("APP_DEBUG")
        or os.environ.get("DEBUG")
    )
    include_debug = parse_bool(debug_env, default=False)
    error_response = handle_api_error(system_error, include_debug=include_debug)
    return JSONResponse(status_code=500, content=error_response)


def _map_error_to_http_status(error: BaseError) -> int:
    """Map internal error categories to HTTP status codes."""
    if error.category == ErrorCategory.VALIDATION:
        return 400
    elif error.category == ErrorCategory.AUTHENTICATION:
        return 401
    elif error.category == ErrorCategory.AUTHORIZATION:
        return 403
    elif (
        error.category == ErrorCategory.BUSINESS
        and error.error_code == ErrorCode.TASK_NOT_FOUND
    ):
        return 404
    elif error.category == ErrorCategory.NETWORK:
        return 502
    elif error.category == ErrorCategory.EXTERNAL_SERVICE:
        return 503
    elif error.category in [ErrorCategory.SYSTEM, ErrorCategory.DATABASE]:
        return 500
    else:
        return 400  # defaulterror


def health_check():
    """System health check"""
    return {"status": "healthy", "service": "AI-Driven Task Orchestration System"}


def llm_health(ping: bool = False):
    """Check LLM service health and configuration.

    Args:
        ping: Whether to perform an actual ping test

    Returns:
        dict: LLM client configuration and ping status
    """
    client = get_default_client()
    info = client.config()
    if ping:
        info["ping_ok"] = client.ping()
    else:
        info["ping_ok"] = None
    return info


def _build_cors_origins() -> list[str]:
    cors_origins_str = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001",
    )
    return [origin.strip() for origin in cors_origins_str.split(",") if origin.strip()]


def _register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(BaseError, base_error_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, general_exception_handler)


def _register_routes(app: FastAPI) -> None:
    for router in get_all_routers():
        app.include_router(router)
    app.add_api_route("/health", health_check, methods=["GET"])
    app.add_api_route("/health/llm", llm_health, methods=["GET"])


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI-Driven Task Orchestration System",
        description="Intelligent Task Orchestration System - Translate natural language goals into executable plans and produce high-quality results",
        version="2.0.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_build_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(ProxyAuthMiddleware)
    _register_exception_handlers(app)
    _register_routes(app)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    from pathlib import Path

    settings = get_settings()
    project_root = Path(__file__).parent.parent.resolve()
    reload_enabled = parse_bool(os.getenv("BACKEND_RELOAD"), default=True)

    run_kwargs = {
        "host": settings.backend_host,
        "port": settings.backend_port,
        "reload": reload_enabled,
    }

    if reload_enabled:
        run_kwargs.update(
            {
                "reload_dirs": [
                    str(project_root / "app"),
                    str(project_root / "tool_box"),
                ],
                "reload_includes": [
                    "app/**/*.py",
                    "tool_box/**/*.py",
                ],
                "reload_excludes": [
                    str(project_root / "runtime"),
                    str(project_root / "data"),
                    "runtime/**",
                    "**/runtime/**",
                    "data/**",
                    "**/data/**",
                    "*.db",
                    "*.sqlite",
                ],
            }
        )

    uvicorn.run("app.main:app", **run_kwargs)
