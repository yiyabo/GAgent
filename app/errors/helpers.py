"""
错误处理辅助函数

为API重构提供便捷的错误创建函数，简化代码维护
遵循DRY原则，减少重复的错误处理代码
"""

from typing import Any, Dict, Optional

from .exceptions import (
    AuthenticationError,
    AuthorizationError,
    BusinessError,
    DatabaseError,
    ErrorCode,
    ExternalServiceError,
    NetworkError,
    SystemError,
    ValidationError,
)


def validation_error(
    message: str,
    field_name: Optional[str] = None,
    error_code: int = ErrorCode.INVALID_FIELD_FORMAT,
    context: Optional[Dict[str, Any]] = None,
    cause: Optional[Exception] = None,
) -> ValidationError:
    """创建验证错误的便捷函数"""
    return ValidationError(message=message, field_name=field_name, error_code=error_code, context=context, cause=cause)


def business_error(
    message: str,
    error_code: int = ErrorCode.BUSINESS_RULE_VIOLATION,
    context: Optional[Dict[str, Any]] = None,
    cause: Optional[Exception] = None,
) -> BusinessError:
    """创建业务错误的便捷函数"""
    return BusinessError(message=message, error_code=error_code, context=context, cause=cause)


def system_error(
    message: str,
    error_code: int = ErrorCode.INTERNAL_SERVER_ERROR,
    context: Optional[Dict[str, Any]] = None,
    cause: Optional[Exception] = None,
) -> SystemError:
    """创建系统错误的便捷函数"""
    return SystemError(message=message, error_code=error_code, context=context, cause=cause)


def not_found_error(
    resource_type: str = "资源", resource_id: Any = None, context: Optional[Dict[str, Any]] = None
) -> BusinessError:
    """创建资源不存在错误的便捷函数"""
    message = f"{resource_type}不存在"
    if resource_id is not None:
        message = f"{resource_type} {resource_id} 不存在"

    return BusinessError(message=message, error_code=ErrorCode.TASK_NOT_FOUND, context=context or {})


def required_field_error(field_names: list, context: Optional[Dict[str, Any]] = None) -> ValidationError:
    """创建必填字段错误的便捷函数"""
    if len(field_names) == 1:
        message = f"缺少必填字段: {field_names[0]}"
    else:
        fields = "、".join(field_names)
        message = f"缺少必填字段: {fields}"

    return ValidationError(
        message=message,
        error_code=ErrorCode.MISSING_REQUIRED_FIELD,
        context=context or {"required_fields": field_names},
        suggestions=["请提供所有必要的参数"],
    )


def invalid_format_error(
    field_name: str, expected_format: str, actual_value: Any = None, context: Optional[Dict[str, Any]] = None
) -> ValidationError:
    """创建格式错误的便捷函数"""
    message = f"字段 '{field_name}' 格式无效，期望格式: {expected_format}"

    format_context = context or {}
    format_context.update({"field_name": field_name, "expected_format": expected_format})
    if actual_value is not None:
        format_context["actual_value"] = str(actual_value)

    return ValidationError(
        message=message, field_name=field_name, error_code=ErrorCode.INVALID_FIELD_FORMAT, context=format_context
    )


def cycle_detection_error(cycle_info: Dict[str, Any]) -> BusinessError:
    """创建循环依赖错误的便捷函数"""
    return BusinessError(
        message="检测到任务依赖环",
        error_code=ErrorCode.INVALID_TASK_STATE,
        context={"cycle_info": cycle_info},
        suggestions=["检查任务依赖关系", "移除循环依赖"],
    )


def not_implemented_error(feature_name: str) -> BusinessError:
    """创建功能未实现错误的便捷函数"""
    return BusinessError(
        message=f"{feature_name}功能尚未实现",
        error_code=ErrorCode.BUSINESS_RULE_VIOLATION,
        suggestions=[f"请等待{feature_name}功能实现"],
    )


def file_operation_error(operation: str, file_path: str, cause: Exception) -> SystemError:
    """创建文件操作错误的便捷函数"""
    return SystemError(
        message=f"文件{operation}失败: {str(cause)}",
        error_code=ErrorCode.INTERNAL_SERVER_ERROR,
        cause=cause,
        context={"operation": operation, "file_path": file_path},
    )


def database_operation_error(
    operation: str,
    table_name: Optional[str] = None,
    cause: Optional[Exception] = None,
    context: Optional[Dict[str, Any]] = None,
) -> DatabaseError:
    """创建数据库操作错误的便捷函数"""
    from .messages import get_error_message

    return DatabaseError(
        message=f"数据库{operation}操作失败",
        operation=operation,
        table_name=table_name,
        error_code=ErrorCode.QUERY_EXECUTION_FAILED,
        cause=cause,
        context=context,
    )


def task_execution_error(task_id: int, operation: str, cause: Optional[Exception] = None) -> SystemError:
    """创建任务执行错误的便捷函数"""
    return SystemError(
        message=f"任务{operation}失败",
        error_code=ErrorCode.INTERNAL_SERVER_ERROR,
        cause=cause,
        context={"task_id": task_id, "operation": operation},
    )


def evaluation_error(
    operation: str, task_id: Optional[int] = None, cause: Optional[Exception] = None
) -> ValidationError:
    """创建评估相关错误的便捷函数"""
    context = {}
    if task_id is not None:
        context["task_id"] = task_id

    return ValidationError(
        message=f"评估{operation}失败", error_code=ErrorCode.FIELD_VALUE_OUT_OF_RANGE, cause=cause, context=context
    )


def decomposition_error(task_id: int, reason: str, result: Optional[Dict[str, Any]] = None) -> BusinessError:
    """创建任务分解错误的便捷函数"""
    context = {"task_id": task_id}
    if result:
        context["result"] = result

    return BusinessError(
        message=f"任务分解失败: {reason}", error_code=ErrorCode.BUSINESS_RULE_VIOLATION, context=context
    )
