"""
Error Handling Helper Functions

Provides convenient error creation functions for API refactoring, simplifying code maintenance
Follows DRY principle, reducing repetitive error handling code
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
    """Convenience function to create validation error"""
    return ValidationError(message=message, field_name=field_name, error_code=error_code, context=context, cause=cause)


def business_error(
    message: str,
    error_code: int = ErrorCode.BUSINESS_RULE_VIOLATION,
    context: Optional[Dict[str, Any]] = None,
    cause: Optional[Exception] = None,
) -> BusinessError:
    """Convenience function to create business error"""
    return BusinessError(message=message, error_code=error_code, context=context, cause=cause)


def system_error(
    message: str,
    error_code: int = ErrorCode.INTERNAL_SERVER_ERROR,
    context: Optional[Dict[str, Any]] = None,
    cause: Optional[Exception] = None,
) -> SystemError:
    """Convenience function to create system error"""
    return SystemError(message=message, error_code=error_code, context=context, cause=cause)


def not_found_error(
    resource_type: str = "Resource", resource_id: Any = None, context: Optional[Dict[str, Any]] = None
) -> BusinessError:
    """Convenience function to create resource not found error"""
    message = f"{resource_type} not found"
    if resource_id is not None:
        message = f"{resource_type} {resource_id} not found"

    return BusinessError(message=message, error_code=ErrorCode.TASK_NOT_FOUND, context=context or {})


def required_field_error(field_names: list, context: Optional[Dict[str, Any]] = None) -> ValidationError:
    """Convenience function to create required field error"""
    if len(field_names) == 1:
        message = f"Missing required field: {field_names[0]}"
    else:
        fields = ", ".join(field_names)
        message = f"Missing required fields: {fields}"

    return ValidationError(
        message=message,
        error_code=ErrorCode.MISSING_REQUIRED_FIELD,
        context=context or {"required_fields": field_names},
        suggestions=["Please provide all required parameters"],
    )


def invalid_format_error(
    field_name: str, expected_format: str, actual_value: Any = None, context: Optional[Dict[str, Any]] = None
) -> ValidationError:
    """Convenience function to create format error"""
    message = f"Field '{field_name}' has invalid format, expected: {expected_format}"

    format_context = context or {}
    format_context.update({"field_name": field_name, "expected_format": expected_format})
    if actual_value is not None:
        format_context["actual_value"] = str(actual_value)

    return ValidationError(
        message=message, field_name=field_name, error_code=ErrorCode.INVALID_FIELD_FORMAT, context=format_context
    )


def cycle_detection_error(cycle_info: Dict[str, Any]) -> BusinessError:
    """Convenience function to create cycle dependency error"""
    return BusinessError(
        message="Circular task dependency detected",
        error_code=ErrorCode.INVALID_TASK_STATE,
        context={"cycle_info": cycle_info},
        suggestions=["Check task dependencies", "Remove circular dependencies"],
    )


def not_implemented_error(feature_name: str) -> BusinessError:
    """Convenience function to create feature not implemented error"""
    return BusinessError(
        message=f"{feature_name} feature not yet implemented",
        error_code=ErrorCode.BUSINESS_RULE_VIOLATION,
        suggestions=[f"Please wait for {feature_name} feature implementation"],
    )


def file_operation_error(operation: str, file_path: str, cause: Exception) -> SystemError:
    """Convenience function to create file operation error"""
    return SystemError(
        message=f"File {operation} failed: {str(cause)}",
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
    """Convenience function to create database operation error"""
    from .messages import get_error_message

    return DatabaseError(
        message=f"Database {operation} operation failed",
        operation=operation,
        table_name=table_name,
        error_code=ErrorCode.QUERY_EXECUTION_FAILED,
        cause=cause,
        context=context,
    )


def task_execution_error(task_id: int, operation: str, cause: Optional[Exception] = None) -> SystemError:
    """Convenience function to create task execution error"""
    return SystemError(
        message=f"Task {operation} failed",
        error_code=ErrorCode.INTERNAL_SERVER_ERROR,
        cause=cause,
        context={"task_id": task_id, "operation": operation},
    )


def evaluation_error(
    operation: str, task_id: Optional[int] = None, cause: Optional[Exception] = None
) -> ValidationError:
    """Convenience function to create evaluation error"""
    context = {}
    if task_id is not None:
        context["task_id"] = task_id

    return ValidationError(
        message=f"Evaluation {operation} failed", error_code=ErrorCode.FIELD_VALUE_OUT_OF_RANGE, cause=cause, context=context
    )


def decomposition_error(task_id: int, reason: str, result: Optional[Dict[str, Any]] = None) -> BusinessError:
    """Convenience function to create task decomposition error"""
    context = {"task_id": task_id}
    if result:
        context["result"] = result

    return BusinessError(
        message=f"Task decomposition failed: {reason}", error_code=ErrorCode.BUSINESS_RULE_VIOLATION, context=context
    )
