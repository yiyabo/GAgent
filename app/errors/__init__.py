"""
Error handling system for the application.

This module provides a unified error handling framework including:
- Custom exception classes
- Error message management
- Error response formatting
- Error handling utilities
"""

from .exceptions import (
    BaseError, ValidationError, BusinessError, SystemError, 
    DatabaseError, NetworkError, AuthenticationError, 
    AuthorizationError, ExternalServiceError,
    ErrorCode, ErrorCategory, ErrorSeverity
)
from .handlers import (
    ErrorHandler, ErrorResponseFormatter, OutputFormat,
    handle_api_error, handle_cli_error, handle_log_error
)
from .messages import get_error_message, Language
from .helpers import validation_error, business_error

__all__ = [
    # Exception classes
    'BaseError', 'ValidationError', 'BusinessError', 'SystemError',
    'DatabaseError', 'NetworkError', 'AuthenticationError',
    'AuthorizationError', 'ExternalServiceError',
    'ErrorCode', 'ErrorCategory', 'ErrorSeverity',
    
    # Error handlers
    'ErrorHandler', 'ErrorResponseFormatter', 'OutputFormat',
    'handle_api_error', 'handle_cli_error', 'handle_log_error',
    
    # Error messages
    'get_error_message', 'Language',
    
    # Helper functions
    'validation_error', 'business_error'
]
