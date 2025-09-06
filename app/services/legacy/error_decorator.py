"""
服务层错误处理装饰器。

为服务层方法提供统一的异常处理和转换机制
遵循AOP模式和装饰器模式
"""

import functools
import logging
from typing import Any, Callable, Optional, Type, Union

from ..errors import (
    BaseError,
    BusinessError,
    DatabaseError,
    ErrorCategory,
    ErrorCode,
    ExternalServiceError,
    NetworkError,
    SystemError,
    ValidationError,
)


def service_error_handler(
    default_error_type: Type[BaseError] = SystemError,
    default_error_code: int = ErrorCode.INTERNAL_SERVER_ERROR,
    log_errors: bool = True,
    reraise_custom_errors: bool = True,
):
    """
    服务层错误处理装饰器

    Args:
        default_error_type: 默认错误类型
        default_error_code: 默认错误码
        log_errors: 是否记录错误日志
        reraise_custom_errors: 是否重新抛出自定义错误
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except BaseError:
                if reraise_custom_errors:
                    raise  # Re-raise custom exceptions
            except Exception as e:
                if log_errors:
                    logger = logging.getLogger(func.__module__)
                    logger.error(f"Service error in {func.__name__}: {str(e)}", exc_info=True)

                # 根据异常类型进行转换
                converted_error = _convert_exception(e, default_error_type, default_error_code, func.__name__)
                raise converted_error

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except BaseError:
                if reraise_custom_errors:
                    raise  # Re-raise custom exceptions
            except Exception as e:
                if log_errors:
                    logger = logging.getLogger(func.__module__)
                    logger.error(f"Service error in {func.__name__}: {str(e)}", exc_info=True)

                # 根据异常类型进行转换
                converted_error = _convert_exception(e, default_error_type, default_error_code, func.__name__)
                raise converted_error

        # 根据函数是否为异步选择合适的包装器
        if hasattr(func, "__code__") and func.__code__.co_flags & 0x80:
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


def database_service_error(func: Callable) -> Callable:
    """数据库服务专用错误处理装饰器"""
    return service_error_handler(default_error_type=DatabaseError, default_error_code=ErrorCode.QUERY_EXECUTION_FAILED)(
        func
    )


def llm_service_error(func: Callable) -> Callable:
    """LLM服务专用错误处理装饰器"""
    return service_error_handler(
        default_error_type=ExternalServiceError, default_error_code=ErrorCode.LLM_SERVICE_ERROR
    )(func)


def embedding_service_error(func: Callable) -> Callable:
    """嵌入向量服务专用错误处理装饰器"""
    return service_error_handler(
        default_error_type=ExternalServiceError, default_error_code=ErrorCode.EMBEDDING_SERVICE_ERROR
    )(func)


def memory_service_error(func: Callable) -> Callable:
    """记忆服务专用错误处理装饰器"""
    return service_error_handler(default_error_type=SystemError, default_error_code=ErrorCode.MCP_SERVICE_ERROR)(func)


def _convert_exception(
    original_exception: Exception, default_error_type: Type[BaseError], default_error_code: int, function_name: str
) -> BaseError:
    """
    将标准异常转换为自定义异常

    Args:
        original_exception: 原始异常
        default_error_type: 默认错误类型
        default_error_code: 默认错误码
        function_name: 函数名称

    Returns:
        转换后的自定义异常
    """
    exception_message = str(original_exception)

    # 数据库相关异常
    if _is_database_exception(original_exception):
        return DatabaseError(
            message=f"Database operation failed: {exception_message}",
            operation=function_name,
            error_code=ErrorCode.QUERY_EXECUTION_FAILED,
            cause=original_exception,
        )

    # 网络相关异常
    if _is_network_exception(original_exception):
        return NetworkError(
            message=f"Network request failed: {exception_message}",
            error_code=ErrorCode.CONNECTION_FAILED,
            cause=original_exception,
        )

    # 外部服务相关异常
    if _is_external_service_exception(original_exception):
        return ExternalServiceError(
            message=f"External service call failed: {exception_message}",
            service_name=_extract_service_name(original_exception),
            error_code=ErrorCode.LLM_SERVICE_ERROR,
            cause=original_exception,
        )

    # 业务逻辑异常
    if _is_business_exception(original_exception):
        return BusinessError(
            message=f"Business operation failed: {exception_message}",
            error_code=ErrorCode.BUSINESS_RULE_VIOLATION,
            cause=original_exception,
        )

    # 默认系统异常
    return default_error_type(
        message=f"{function_name} execution failed: {exception_message}",
        error_code=default_error_code,
        cause=original_exception,
    )


def _is_database_exception(exception: Exception) -> bool:
    """判断是否为数据库相关异常"""
    exception_type = type(exception).__name__.lower()
    error_message = str(exception).lower()

    database_indicators = [
        "sqlite",
        "database",
        "cursor",
        "sql",
        "connection",
        "integrity",
        "constraint",
        "foreign key",
        "unique",
    ]

    return any(indicator in exception_type for indicator in database_indicators) or any(
        indicator in error_message for indicator in database_indicators
    )


def _is_network_exception(exception: Exception) -> bool:
    """判断是否为网络相关异常"""
    exception_type = type(exception).__name__.lower()
    error_message = str(exception).lower()

    network_indicators = ["connection", "timeout", "http", "requests", "urllib", "socket", "ssl", "certificate", "dns"]

    return any(indicator in exception_type for indicator in network_indicators) or any(
        indicator in error_message for indicator in network_indicators
    )


def _is_external_service_exception(exception: Exception) -> bool:
    """判断是否为外部服务相关异常"""
    exception_type = type(exception).__name__.lower()
    error_message = str(exception).lower()

    service_indicators = [
        "api",
        "client",
        "service",
        "token",
        "auth",
        "rate limit",
        "quota",
        "llm",
        "openai",
        "anthropic",
        "embedding",
    ]

    return any(indicator in exception_type for indicator in service_indicators) or any(
        indicator in error_message for indicator in service_indicators
    )


def _is_business_exception(exception: Exception) -> bool:
    """判断是否为业务逻辑异常"""
    exception_types = [ValueError, AssertionError, TypeError]
    return type(exception) in exception_types


def _extract_service_name(exception: Exception) -> Optional[str]:
    """从异常中提取服务名称"""
    error_message = str(exception).lower()

    service_mapping = {
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "llm": "LLM Service",
        "embedding": "Embedding Service",
        "mcp": "MCP Service",
    }

    for keyword, service_name in service_mapping.items():
        if keyword in error_message:
            return service_name

    return None


# 通用错误处理函数，用于非装饰器场景


def handle_service_error(func: Callable, *args, **kwargs):
    """
    通用服务错误处理函数

    用于不适合使用装饰器的场景
    """
    try:
        return func(*args, **kwargs)
    except BaseError:
        raise  # 重新抛出自定义异常
    except Exception as e:
        logger = logging.getLogger(func.__module__ if hasattr(func, "__module__") else __name__)
        logger.error(
            f"Service error in {func.__name__ if hasattr(func, '__name__') else 'function'}: {str(e)}", exc_info=True
        )

        converted_error = _convert_exception(
            e, SystemError, ErrorCode.INTERNAL_SERVER_ERROR, func.__name__ if hasattr(func, "__name__") else "function"
        )
        raise converted_error


async def handle_async_service_error(func: Callable, *args, **kwargs):
    """
    异步服务错误处理函数

    用于异步函数的错误处理
    """
    try:
        return await func(*args, **kwargs)
    except BaseError:
        raise  # 重新抛出自定义异常
    except Exception as e:
        logger = logging.getLogger(func.__module__ if hasattr(func, "__module__") else __name__)
        logger.error(
            f"Async service error in {func.__name__ if hasattr(func, '__name__') else 'function'}: {str(e)}",
            exc_info=True,
        )

        converted_error = _convert_exception(
            e, SystemError, ErrorCode.INTERNAL_SERVER_ERROR, func.__name__ if hasattr(func, "__name__") else "function"
        )
        raise converted_error
