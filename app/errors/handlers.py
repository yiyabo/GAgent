"""
Unified Error Response Format Handler

Implements standardized error response format, supporting both API and CLI output modes
Follows SOLID and DRY principles
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
    """Output format enumeration"""

    JSON = "json"
    CLI = "cli"
    LOG = "log"


@dataclass
class ErrorResponse:
    """Standardized error response data structure"""

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
    Error Response Formatter

    Follows Single Responsibility Principle: Dedicated to error formatting
    Follows Open-Closed Principle: Extensible for new output formats
    """

    @staticmethod
    def format_for_api(error: BaseError, include_debug: bool = False) -> Dict[str, Any]:
        """
        Format as API response

        Args:
            error: Base error instance
            include_debug: Whether to include debug information

        Returns:
            Standardized API error response dictionary
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

        # Include debug info in development environment
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
        Format as CLI-friendly error output

        Args:
            error: Base error instance
            verbose: Whether to show detailed information

        Returns:
            CLI-formatted error message string
        """
        # Choose icon based on severity
        severity_icons = {
            ErrorSeverity.LOW: "ℹ️",
            ErrorSeverity.MEDIUM: "⚠️",
            ErrorSeverity.HIGH: "❌",
            ErrorSeverity.CRITICAL: "🚨",
        }

        icon = severity_icons.get(error.severity, "❌")

        # Basic error information
        lines = [
            f"{icon} Error [{error.error_code}]: {error.message}",
            f"   Category: {error.category.value}",
            f"   Severity: {error.severity.value}",
            f"   Error ID: {error.error_id}",
        ]

        # Context information
        if error.context:
            lines.append("Context:")
            for key, value in error.context.items():
                lines.append(f"     - {key}: {value}")

        # Suggestions
        if error.suggestions:
            lines.append("Suggestions:")
            for suggestion in error.suggestions:
                lines.append(f"     • {suggestion}")

        # Show extra info in verbose mode
        if verbose:
            lines.append(f"Time: {error.timestamp.strftime('%Y-%m-%d %H:%M:%S')}")

            if error.cause:
                lines.append(f"Root cause: {type(error.cause).__name__}: {error.cause}")

        return "\n".join(lines)

    @staticmethod
    def format_for_log(error: BaseError) -> Dict[str, Any]:
        """
        Format as structured log format

        Args:
            error: Base error instance

        Returns:
            Structured log data dictionary
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
    Unified Error Handler

    Implements Dependency Inversion Principle: Depends on abstract error interface rather than concrete implementations
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
        Unified exception handling

        Args:
            exception: Exception instance
            output_format: Output format, defaults to format set during initialization
            include_debug: Whether to include debug information
            verbose: Whether to show detailed information in CLI mode

        Returns:
            Formatted error response
        """
        # Convert to unified BaseError format
        if isinstance(exception, BaseError):
            error = exception
        else:
            # Convert standard exception to SystemError
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
        Batch process validation errors

        Args:
            validation_errors: List of validation errors
            output_format: Output format

        Returns:
            Formatted batch validation error response
        """
        if not validation_errors:
            return self.handle_exception(ValidationError("Unknown validation error"))

        # Create combined validation error
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


# Global error handler instances
api_error_handler = ErrorHandler(OutputFormat.JSON)
cli_error_handler = ErrorHandler(OutputFormat.CLI)
log_error_handler = ErrorHandler(OutputFormat.LOG)


def handle_api_error(exception: Exception, include_debug: bool = False) -> Dict[str, Any]:
    """Convenience function for API error handling"""
    return api_error_handler.handle_exception(exception, include_debug=include_debug)


def handle_cli_error(exception: Exception, verbose: bool = False) -> str:
    """Convenience function for CLI error handling"""
    return cli_error_handler.handle_exception(exception, verbose=verbose)


def handle_log_error(exception: Exception) -> Dict[str, Any]:
    """Convenience function for log error handling"""
    return log_error_handler.handle_exception(exception)


# Error handling decorator implementing AOP pattern

from functools import wraps


def handle_errors(output_format: OutputFormat = OutputFormat.JSON, reraise: bool = False, log_errors: bool = True):
    """
    Error handling decorator

    Args:
        output_format: Output format
        reraise: Whether to re-raise the exception
        log_errors: Whether to log errors
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                # Log the error
                if log_errors:
                    import logging

                    logger = logging.getLogger(func.__module__)
                    log_data = handle_log_error(e)
                    logger.error(f"Function {func.__name__} failed", extra=log_data)

                # Handle error response
                handler = ErrorHandler(output_format)
                error_response = handler.handle_exception(e)

                if reraise:
                    raise

                return error_response

        return wrapper

    return decorator


def safe_execute(func, *args, default_return=None, log_errors=True, **kwargs):
    """
    Safely execute function, catching all exceptions

    Args:
        func: Function to execute
        *args: Function arguments
        default_return: Default return value on exception
        log_errors: Whether to log errors
        **kwargs: Function keyword arguments

    Returns:
        Function execution result or default value
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
