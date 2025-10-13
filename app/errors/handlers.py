"""
统一错误响应格式处理器

实现标准化的错误响应格式，支持API和CLI两种输出模式
遵循SOLID原则和DRY原则
"""

import sys
import traceback
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from .exceptions import (
    AuthenticationError,
    AuthorizationError,
    BaseError,
    BusinessError,
    DatabaseError,
    ErrorCategory,
    ErrorCode,
    ErrorSeverity,
    ExternalServiceError,
    NetworkError,
    SystemError,
    ValidationError,
)


class OutputFormat(Enum):
    """输出格式枚举"""

    JSON = "json"
    CLI = "cli"
    LOG = "log"


@dataclass
class ErrorResponse:
    """标准化错误响应数据结构"""

    success: bool = False
    error_id: str = ""
    error_code: int = 0
    message: str = ""
    category: str = ""
    severity: str = ""
    timestamp: str = ""
    context: Dict[str, Any] = None
    suggestions: List[str] = None
    debug_info: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if self.context is None:
            self.context = {}
        if self.suggestions is None:
            self.suggestions = []


class ErrorResponseFormatter:
    """
    错误响应格式化器

    遵循单一职责原则：专门负责错误格式化
    遵循开闭原则：可扩展新的输出格式
    """

    @staticmethod
    def format_for_api(error: BaseError, include_debug: bool = False) -> Dict[str, Any]:
        """
        格式化为API响应格式

        Args:
            error: 基础错误实例
            include_debug: 是否包含调试信息

        Returns:
            标准化的API错误响应字典
        """
        response_data = {
            "success": False,
            "error": {
                "error_id": error.error_id,
                "error_code": error.error_code,
                "message": error.message,
                "category": error.category.value,
                "severity": error.severity.value,
                "timestamp": error.timestamp.isoformat(),
                "context": error.context,
                "suggestions": error.suggestions,
            },
        }

        # 开发环境包含调试信息
        if include_debug and error.cause:
            response_data["error"]["debug_info"] = {
                "cause_type": type(error.cause).__name__,
                "cause_message": str(error.cause),
                "traceback": (
                    traceback.format_exception(type(error.cause), error.cause, error.cause.__traceback__)
                    if error.cause.__traceback__
                    else None
                ),
            }

        return response_data

    @staticmethod
    def format_for_cli(error: BaseError, verbose: bool = False) -> str:
        """
        格式化为CLI友好的错误输出

        Args:
            error: 基础错误实例
            verbose: 是否显示详细信息

        Returns:
            CLI格式的错误消息字符串
        """
        # 根据严重程度选择颜色和图标
        severity_icons = {
            ErrorSeverity.LOW: "ℹ️",
            ErrorSeverity.MEDIUM: "⚠️",
            ErrorSeverity.HIGH: "❌",
            ErrorSeverity.CRITICAL: "🚨",
        }

        icon = severity_icons.get(error.severity, "❌")

        # 基础错误信息
        lines = [
            f"{icon} Error [{error.error_code}]: {error.message}",
            f"   Category: {error.category.value}",
            f"   Severity: {error.severity.value}",
            f"   Error ID: {error.error_id}",
        ]

        # 上下文信息
        if error.context:
            lines.append("Context:")
            for key, value in error.context.items():
                lines.append(f"     - {key}: {value}")

        # 建议信息
        if error.suggestions:
            lines.append("Suggestions:")
            for suggestion in error.suggestions:
                lines.append(f"     • {suggestion}")

        # 详细模式显示额外信息
        if verbose:
            lines.append(f"Time: {error.timestamp.strftime('%Y-%m-%d %H:%M:%S')}")

            if error.cause:
                lines.append(f"Root cause: {type(error.cause).__name__}: {error.cause}")

        return "\n".join(lines)

    @staticmethod
    def format_for_log(error: BaseError) -> Dict[str, Any]:
        """
        格式化为结构化日志格式

        Args:
            error: 基础错误实例

        Returns:
            结构化日志数据字典
        """
        log_data = {
            "event": "error_occurred",
            "error_id": error.error_id,
            "error_code": error.error_code,
            "message": error.message,
            "category": error.category.value,
            "severity": error.severity.value,
            "timestamp": error.timestamp.isoformat(),
            "context": error.context,
        }

        if error.cause:
            log_data["cause"] = {"type": type(error.cause).__name__, "message": str(error.cause)}

        return log_data


class ErrorHandler:
    """
    统一错误处理器

    实现依赖倒置原则：依赖抽象的错误接口而不是具体实现
    """

    def __init__(self, default_format: OutputFormat = OutputFormat.JSON):
        self.default_format = default_format
        self.formatter = ErrorResponseFormatter()

    def handle_exception(
        self,
        exception: Exception,
        output_format: Optional[OutputFormat] = None,
        include_debug: bool = False,
        verbose: bool = False,
    ) -> Union[Dict[str, Any], str]:
        """
        统一处理异常

        Args:
            exception: 异常实例
            output_format: 输出格式，默认使用初始化时设置的格式
            include_debug: 是否包含调试信息
            verbose: CLI模式下是否显示详细信息

        Returns:
            格式化后的错误响应
        """
        # 转换为统一的BaseError格式
        if isinstance(exception, BaseError):
            error = exception
        else:
            # 将标准异常转换为SystemError
            error = SystemError(message=str(exception), cause=exception, severity=ErrorSeverity.HIGH)

        format_type = output_format or self.default_format

        if format_type == OutputFormat.JSON:
            return self.formatter.format_for_api(error, include_debug)
        elif format_type == OutputFormat.CLI:
            return self.formatter.format_for_cli(error, verbose)
        elif format_type == OutputFormat.LOG:
            return self.formatter.format_for_log(error)
        else:
            raise ValueError(f"Unsupported output format: {format_type}")

    def handle_validation_errors(
        self, validation_errors: List[Dict[str, Any]], output_format: Optional[OutputFormat] = None
    ) -> Union[Dict[str, Any], str]:
        """
        批量处理验证错误

        Args:
            validation_errors: 验证错误列表
            output_format: 输出格式

        Returns:
            格式化后的批量验证错误响应
        """
        if not validation_errors:
            return self.handle_exception(ValidationError("Unknown validation error"))

        # 创建综合验证错误
        error_messages = []
        all_context = {}

        for i, error_data in enumerate(validation_errors):
            field_name = error_data.get("field_name", f"field_{i}")
            message = error_data.get("message", "Validation failed")
            error_messages.append(f"{field_name}: {message}")

            if "context" in error_data:
                all_context[field_name] = error_data["context"]

        combined_message = f"Data validation failed ({len(validation_errors)} errors): " + "; ".join(error_messages)

        validation_error = ValidationError(
            message=combined_message,
            context=all_context,
            suggestions=[
                "Check all marked fields",
                "Ensure data format meets requirements",
                "Refer to API documentation for data correction",
            ],
        )

        return self.handle_exception(validation_error, output_format)


# 全局错误处理器实例
api_error_handler = ErrorHandler(OutputFormat.JSON)
cli_error_handler = ErrorHandler(OutputFormat.CLI)
log_error_handler = ErrorHandler(OutputFormat.LOG)


def handle_api_error(exception: Exception, include_debug: bool = False) -> Dict[str, Any]:
    """API错误处理便捷函数"""
    return api_error_handler.handle_exception(exception, include_debug=include_debug)


def handle_cli_error(exception: Exception, verbose: bool = False) -> str:
    """CLI错误处理便捷函数"""
    return cli_error_handler.handle_exception(exception, verbose=verbose)


def handle_log_error(exception: Exception) -> Dict[str, Any]:
    """日志错误处理便捷函数"""
    return log_error_handler.handle_exception(exception)


# 错误处理装饰器，实现AOP模式

from functools import wraps


def handle_errors(output_format: OutputFormat = OutputFormat.JSON, reraise: bool = False, log_errors: bool = True):
    """
    错误处理装饰器

    Args:
        output_format: 输出格式
        reraise: 是否重新抛出异常
        log_errors: 是否记录错误日志
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                # 记录错误日志
                if log_errors:
                    import logging

                    logger = logging.getLogger(func.__module__)
                    log_data = handle_log_error(e)
                    logger.error(f"Function {func.__name__} failed", extra=log_data)

                # 处理错误响应
                handler = ErrorHandler(output_format)
                error_response = handler.handle_exception(e)

                if reraise:
                    raise

                return error_response

        return wrapper

    return decorator


def safe_execute(func, *args, default_return=None, log_errors=True, **kwargs):
    """
    安全执行函数，捕获所有异常

    Args:
        func: 要执行的函数
        *args: 函数参数
        default_return: 异常时的默认返回值
        log_errors: 是否记录错误
        **kwargs: 函数关键字参数

    Returns:
        函数执行结果或默认值
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        if log_errors:
            import logging

            logger = logging.getLogger(func.__module__ if hasattr(func, "__module__") else __name__)
            log_data = handle_log_error(e)
            logger.error(f"Safe execution failed for {func}", extra=log_data)

        return default_return
