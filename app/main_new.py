"""FastAPI main application module for AI-Driven Task Orchestration System.

This module provides the main FastAPI application entry point with centralized
configuration, lifecycle management, and route registration.
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from tool_box import initialize_toolbox

from .database import init_db
from .database_pool import get_db
from .errors import BaseError, BusinessError, ErrorCode, ValidationError, handle_api_error
from .errors.exceptions import ErrorCategory
from .errors.exceptions import SystemError as CustomSystemError
from .llm import get_default_client
from .services.logging_config import setup_logging
from .services.settings import get_settings
from .utils.route_helpers import parse_bool

# Import router function
from .routers import get_all_routers

# Memory system integration
from .api.memory_api import memory_router


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
        logging.getLogger("app.main").info("Tool Box integrated successfully - Enhanced AI capabilities enabled")
    except (ValueError, TypeError) as e:
        logging.getLogger("app.main").warning("Tool Box initialization failed: %s", e)

    yield


# Create FastAPI application
app = FastAPI(
    title="AI-Driven Task Orchestration System",
    description="智能任务编排系统 - 将自然语言目标转为可执行计划并产出高质量结果",
    version="2.0.0",
    lifespan=lifespan
)


# 注册统一异常处理器
@app.exception_handler(BaseError)
async def base_error_handler(_request: Request, exc: BaseError):
    """统一处理自定义业务异常."""
    error_response = handle_api_error(exc, include_debug=False)
    return JSONResponse(status_code=_map_error_to_http_status(exc), content=error_response)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, exc: RequestValidationError):
    """处理FastAPI参数验证错误."""
    validation_error = ValidationError(
        message="Request parameter validation failed",
        error_code=ErrorCode.SCHEMA_VALIDATION_FAILED,
        context={"errors": exc.errors(), "body": str(exc.body) if exc.body else None},
        suggestions=["检查请求参数格式", "确保必填字段完整", "参考API文档修正参数"],
    )
    error_response = handle_api_error(validation_error, include_debug=False)
    return JSONResponse(status_code=422, content=error_response)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """处理HTTP异常."""
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


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """处理未捕获的通用异常."""
    system_error = CustomSystemError(
        message="Internal server error",
        error_code=ErrorCode.INTERNAL_SERVER_ERROR,
        cause=exc,
        context={"path": str(request.url), "method": request.method, "exception_type": type(exc).__name__},
        suggestions=["稍后重试", "如问题持续存在，请联系技术支持"],
    )
    # 根据环境变量控制是否返回调试信息（默认生产环境关闭）
    debug_env = os.environ.get("API_DEBUG") or os.environ.get("APP_DEBUG") or os.environ.get("DEBUG")
    include_debug = parse_bool(debug_env, default=False)
    error_response = handle_api_error(system_error, include_debug=include_debug)
    return JSONResponse(status_code=500, content=error_response)


def _map_error_to_http_status(error: BaseError) -> int:
    """将自定义错误映射到HTTP状态码."""
    if error.category == ErrorCategory.VALIDATION:
        return 400
    elif error.category == ErrorCategory.AUTHENTICATION:
        return 401
    elif error.category == ErrorCategory.AUTHORIZATION:
        return 403
    elif error.category == ErrorCategory.BUSINESS and error.error_code == ErrorCode.TASK_NOT_FOUND:
        return 404
    elif error.category == ErrorCategory.NETWORK:
        return 502
    elif error.category == ErrorCategory.EXTERNAL_SERVICE:
        return 503
    elif error.category in [ErrorCategory.SYSTEM, ErrorCategory.DATABASE]:
        return 500
    else:
        return 400  # 默认客户端错误


# 注册所有路由
app.include_router(memory_router)           # Memory system

# 注册功能模块路由
for router in get_all_routers():
    app.include_router(router)


# Health check endpoint
@app.get("/health")
def health_check():
    """System health check"""
    return {"status": "healthy", "service": "AI-Driven Task Orchestration System"}


@app.get("/health/llm")
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
