"""
Error handling system for the application.

This module provides a unified error handling framework including:
- Custom exception classes
- Error message management
- Error response formatting
- Error handling utilities
"""

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
from .handlers import (
    ErrorHandler,
    ErrorResponseFormatter,
    OutputFormat,
    handle_api_error,
    handle_cli_error,
    handle_log_error,
)
from .helpers import business_error, validation_error
from .messages import Language, get_error_message

__all__ = [
    # Exception classes
    "BaseError",
    "ValidationError",
    "BusinessError",
    "SystemError",
    "DatabaseError",
    "NetworkError",
    "AuthenticationError",
    "AuthorizationError",
    "ExternalServiceError",
    "ErrorCode",
    "ErrorCategory",
    "ErrorSeverity",
    # Error handlers
    "ErrorHandler",
    "ErrorResponseFormatter",
    "OutputFormat",
    "handle_api_error",
    "handle_cli_error",
    "handle_log_error",
    # Error messages
    "get_error_message",
    "Language",
    # Helper functions
    "validation_error",
    "business_error",
]
