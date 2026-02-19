"""
Exception system.

Defines structured error types with codes, categories, and severity levels.
Design follows SOLID principles:
- SRP: Each exception class has one responsibility.
- OCP: New error types can be added without modifying existing ones.
- DIP: Higher layers depend on abstractions rather than implementation details.
"""

import logging
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class ErrorSeverity(Enum):
    """Error severity levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ErrorCategory(Enum):
    """Error category taxonomy."""

    BUSINESS = "business"
    VALIDATION = "validation"
    SYSTEM = "system"
    NETWORK = "network"
    DATABASE = "database"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    EXTERNAL_SERVICE = "external_service"


class ErrorCode:
    """Global error code definitions."""

    BUSINESS_RULE_VIOLATION = 1001
    TASK_NOT_FOUND = 1002
    INVALID_TASK_STATE = 1003
    INSUFFICIENT_RESOURCES = 1004
    GOAL_VALIDATION_FAILED = 1005

    MISSING_REQUIRED_FIELD = 2001
    INVALID_FIELD_FORMAT = 2002
    FIELD_VALUE_OUT_OF_RANGE = 2003
    INVALID_JSON_FORMAT = 2004
    SCHEMA_VALIDATION_FAILED = 2005

    INTERNAL_SERVER_ERROR = 3001
    SERVICE_UNAVAILABLE = 3002
    TIMEOUT_ERROR = 3003
    CONFIGURATION_ERROR = 3004
    MEMORY_INSUFFICIENT = 3005

    CONNECTION_FAILED = 4001
    REQUEST_TIMEOUT = 4002
    HTTP_CLIENT_ERROR = 4003
    HTTP_SERVER_ERROR = 4004

    DATABASE_CONNECTION_FAILED = 5001
    QUERY_EXECUTION_FAILED = 5002
    TRANSACTION_FAILED = 5003
    CONSTRAINT_VIOLATION = 5004
    DATA_INTEGRITY_ERROR = 5005

    AUTHENTICATION_FAILED = 6001
    TOKEN_EXPIRED = 6002
    INSUFFICIENT_PERMISSIONS = 6003
    INVALID_CREDENTIALS = 6004

    LLM_SERVICE_ERROR = 7001
    EMBEDDING_SERVICE_ERROR = 7002
    MCP_SERVICE_ERROR = 7003
    API_RATE_LIMIT_EXCEEDED = 7004


class BaseError(Exception):
    """
    Base class for all structured application errors.

    Captures contextual metadata and logs itself on creation.
    """

    def __init__(
        self,
        message: str,
        error_code: int,
        category: ErrorCategory,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        context: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None,
        suggestions: Optional[List[str]] = None,
    ):
        super().__init__(message)

        self.message = message
        self.error_code = error_code
        self.category = category
        self.severity = severity

        self.error_id = str(uuid.uuid4())
        self.timestamp = datetime.now()

        self.context = context or {}
        self.cause = cause
        self.suggestions = suggestions or []

        self._log_error()

    def _log_error(self):
        """Log error with severity-aware log level."""
        logger = logging.getLogger(self.__class__.__module__)

        log_data = {
            "error_id": self.error_id,
            "error_code": self.error_code,
            "category": self.category.value,
            "severity": self.severity.value,
            "message": self.message,
            "context": self.context,
        }

        if self.severity == ErrorSeverity.CRITICAL:
            logger.critical(f"Critical Error: {log_data}")
        elif self.severity == ErrorSeverity.HIGH:
            logger.error(f"High Severity Error: {log_data}")
        elif self.severity == ErrorSeverity.MEDIUM:
            logger.warning(f"Medium Severity Error: {log_data}")
        else:
            logger.info(f"Low Severity Error: {log_data}")

    def to_dict(self) -> Dict[str, Any]:
        """Serialize error as API-safe dictionary."""
        return {
            "error_id": self.error_id,
            "error_code": self.error_code,
            "category": self.category.value,
            "severity": self.severity.value,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "context": self.context,
            "suggestions": self.suggestions,
        }

    def __str__(self) -> str:
        return f"[{self.error_code}] {self.category.value}: {self.message}"


class BusinessError(BaseError):
    """
    Business rule violation error.

    Use when domain/business constraints are not satisfied.
    """

    def __init__(
        self,
        message: str,
        error_code: int = ErrorCode.BUSINESS_RULE_VIOLATION,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        context: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None,
        suggestions: Optional[List[str]] = None,
    ):
        super().__init__(
            message=message,
            error_code=error_code,
            category=ErrorCategory.BUSINESS,
            severity=severity,
            context=context,
            cause=cause,
            suggestions=suggestions,
        )


class ValidationError(BaseError):
    """
    Input validation error.

    Use for malformed or missing request parameters.
    """

    def __init__(
        self,
        message: str,
        field_name: Optional[str] = None,
        field_value: Any = None,
        error_code: int = ErrorCode.MISSING_REQUIRED_FIELD,
        severity: ErrorSeverity = ErrorSeverity.LOW,
        context: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None,
        suggestions: Optional[List[str]] = None,
    ):
        validation_context = context or {}
        if field_name:
            validation_context["field_name"] = field_name
        if field_value is not None:
            validation_context["field_value"] = str(field_value)

        super().__init__(
            message=message,
            error_code=error_code,
            category=ErrorCategory.VALIDATION,
            severity=severity,
            context=validation_context,
            cause=cause,
            suggestions=suggestions
            or ["Check request parameters", "Review API parameter documentation"],
        )


class SystemError(BaseError):
    """
    System-level runtime error.

    Use for infrastructure, configuration, or unexpected runtime failures.
    """

    def __init__(
        self,
        message: str,
        error_code: int = ErrorCode.INTERNAL_SERVER_ERROR,
        severity: ErrorSeverity = ErrorSeverity.HIGH,
        context: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None,
        suggestions: Optional[List[str]] = None,
    ):
        super().__init__(
            message=message,
            error_code=error_code,
            category=ErrorCategory.SYSTEM,
            severity=severity,
            context=context,
            cause=cause,
            suggestions=suggestions or ["Retry later", "Check system logs and configuration"],
        )


class NetworkError(BaseError):
    """
    Network/HTTP communication error.

    Use for connection failures, request timeouts, and HTTP transport issues.
    """

    def __init__(
        self,
        message: str,
        url: Optional[str] = None,
        status_code: Optional[int] = None,
        error_code: int = ErrorCode.CONNECTION_FAILED,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        context: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None,
        suggestions: Optional[List[str]] = None,
    ):
        network_context = context or {}
        if url:
            network_context["url"] = url
        if status_code:
            network_context["status_code"] = status_code

        super().__init__(
            message=message,
            error_code=error_code,
            category=ErrorCategory.NETWORK,
            severity=severity,
            context=network_context,
            cause=cause,
            suggestions=suggestions
            or ["Check network connectivity", "Verify remote service status", "Retry request"],
        )


class DatabaseError(BaseError):
    """
    Database operation error.

    Use for connection, query, transaction, or integrity failures.
    """

    def __init__(
        self,
        message: str,
        operation: Optional[str] = None,
        table_name: Optional[str] = None,
        error_code: int = ErrorCode.DATABASE_CONNECTION_FAILED,
        severity: ErrorSeverity = ErrorSeverity.HIGH,
        context: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None,
        suggestions: Optional[List[str]] = None,
    ):
        db_context = context or {}
        if operation:
            db_context["operation"] = operation
        if table_name:
            db_context["table_name"] = table_name

        super().__init__(
            message=message,
            error_code=error_code,
            category=ErrorCategory.DATABASE,
            severity=severity,
            context=db_context,
            cause=cause,
            suggestions=suggestions
            or ["Check database connection/configuration", "Verify database service health", "Review SQL/query"],
        )


class AuthenticationError(BaseError):
    """
    Authentication error.

    Use when identity verification fails.
    """

    def __init__(
        self,
        message: str,
        error_code: int = ErrorCode.AUTHENTICATION_FAILED,
        severity: ErrorSeverity = ErrorSeverity.HIGH,
        context: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None,
        suggestions: Optional[List[str]] = None,
    ):
        super().__init__(
            message=message,
            error_code=error_code,
            category=ErrorCategory.AUTHENTICATION,
            severity=severity,
            context=context,
            cause=cause,
            suggestions=suggestions or ["Verify credentials", "Refresh or reissue authentication token"],
        )


class AuthorizationError(BaseError):
    """
    Authorization error.

    Use when a user lacks required permissions.
    """

    def __init__(
        self,
        message: str,
        required_permission: Optional[str] = None,
        error_code: int = ErrorCode.INSUFFICIENT_PERMISSIONS,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        context: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None,
        suggestions: Optional[List[str]] = None,
    ):
        auth_context = context or {}
        if required_permission:
            auth_context["required_permission"] = required_permission

        super().__init__(
            message=message,
            error_code=error_code,
            category=ErrorCategory.AUTHORIZATION,
            severity=severity,
            context=auth_context,
            cause=cause,
            suggestions=suggestions
            or ["Request required permission", "Verify role/permission configuration"],
        )


class ExternalServiceError(BaseError):
    """
    External dependency/service error.

    Use for failures from LLM, embedding, MCP, or other third-party services.
    """

    def __init__(
        self,
        message: str,
        service_name: Optional[str] = None,
        error_code: int = ErrorCode.LLM_SERVICE_ERROR,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        context: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None,
        suggestions: Optional[List[str]] = None,
    ):
        service_context = context or {}
        if service_name:
            service_context["service_name"] = service_name

        super().__init__(
            message=message,
            error_code=error_code,
            category=ErrorCategory.EXTERNAL_SERVICE,
            severity=severity,
            context=service_context,
            cause=cause,
            suggestions=suggestions
            or ["Check service status", "Verify API credentials/configuration", "Retry request"],
        )




def create_validation_error(field_name: str, message: str, field_value: Any = None) -> ValidationError:
    """Create a standardized validation error."""
    return ValidationError(
        message=f"Field '{field_name}' validation failed: {message}",
        field_name=field_name,
        field_value=field_value,
        error_code=ErrorCode.INVALID_FIELD_FORMAT,
    )


def create_business_error(message: str, context: Optional[Dict[str, Any]] = None) -> BusinessError:
    """Create a standardized business error."""
    return BusinessError(message=message, context=context, error_code=ErrorCode.BUSINESS_RULE_VIOLATION)


def create_system_error(message: str, cause: Optional[Exception] = None) -> SystemError:
    """Create a standardized system error."""
    return SystemError(
        message=message, cause=cause, error_code=ErrorCode.INTERNAL_SERVER_ERROR, severity=ErrorSeverity.HIGH
    )


def create_database_error(
    message: str, operation: str, table_name: Optional[str] = None, cause: Optional[Exception] = None
) -> DatabaseError:
    """Create a standardized database error."""
    return DatabaseError(
        message=message,
        operation=operation,
        table_name=table_name,
        cause=cause,
        error_code=ErrorCode.QUERY_EXECUTION_FAILED,
    )


def create_network_error(
    message: str, url: Optional[str] = None, status_code: Optional[int] = None, cause: Optional[Exception] = None
) -> NetworkError:
    """Create a standardized network error."""
    return NetworkError(
        message=message, url=url, status_code=status_code, cause=cause, error_code=ErrorCode.CONNECTION_FAILED
    )
