"""
统一异常处理系统

实现分层错误分类和标准化错误处理模式，遵循SOLID原则：
- SRP: 每个异常类有单一职责
- OCP: 可扩展新的错误类型
- DIP: 依赖抽象的错误接口
"""

import logging
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class ErrorSeverity(Enum):
    """错误严重程度分级"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ErrorCategory(Enum):
    """错误分类枚举"""

    BUSINESS = "business"
    VALIDATION = "validation"
    SYSTEM = "system"
    NETWORK = "network"
    DATABASE = "database"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    EXTERNAL_SERVICE = "external_service"


class ErrorCode:
    """统一错误码定义"""

    # 业务错误 (1000-1999)
    BUSINESS_RULE_VIOLATION = 1001
    TASK_NOT_FOUND = 1002
    INVALID_TASK_STATE = 1003
    INSUFFICIENT_RESOURCES = 1004
    GOAL_VALIDATION_FAILED = 1005

    # 验证错误 (2000-2999)
    MISSING_REQUIRED_FIELD = 2001
    INVALID_FIELD_FORMAT = 2002
    FIELD_VALUE_OUT_OF_RANGE = 2003
    INVALID_JSON_FORMAT = 2004
    SCHEMA_VALIDATION_FAILED = 2005

    # 系统错误 (3000-3999)
    INTERNAL_SERVER_ERROR = 3001
    SERVICE_UNAVAILABLE = 3002
    TIMEOUT_ERROR = 3003
    CONFIGURATION_ERROR = 3004
    MEMORY_INSUFFICIENT = 3005

    # 网络错误 (4000-4999)
    CONNECTION_FAILED = 4001
    REQUEST_TIMEOUT = 4002
    HTTP_CLIENT_ERROR = 4003
    HTTP_SERVER_ERROR = 4004

    # 数据库错误 (5000-5999)
    DATABASE_CONNECTION_FAILED = 5001
    QUERY_EXECUTION_FAILED = 5002
    TRANSACTION_FAILED = 5003
    CONSTRAINT_VIOLATION = 5004
    DATA_INTEGRITY_ERROR = 5005

    # 认证授权错误 (6000-6999)
    AUTHENTICATION_FAILED = 6001
    TOKEN_EXPIRED = 6002
    INSUFFICIENT_PERMISSIONS = 6003
    INVALID_CREDENTIALS = 6004

    # 外部服务错误 (7000-7999)
    LLM_SERVICE_ERROR = 7001
    EMBEDDING_SERVICE_ERROR = 7002
    MCP_SERVICE_ERROR = 7003
    API_RATE_LIMIT_EXCEEDED = 7004


class BaseError(Exception):
    """
    基础错误类，遵循单一职责原则

    所有自定义异常的基类，提供统一的错误处理接口
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

        # 核心错误信息
        self.message = message
        self.error_code = error_code
        self.category = category
        self.severity = severity

        # 错误追踪信息
        self.error_id = str(uuid.uuid4())
        self.timestamp = datetime.now()

        # 上下文和调试信息
        self.context = context or {}
        self.cause = cause
        self.suggestions = suggestions or []

        # 自动记录错误
        self._log_error()

    def _log_error(self):
        """自动记录错误到日志系统"""
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
        """转换为字典格式，用于API响应"""
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
    业务逻辑错误

    用于业务规则违反、状态不合法等业务层面的错误
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
    数据验证错误

    用于输入参数验证、数据格式校验等错误
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
        # 扩展上下文信息
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
            suggestions=suggestions or ["检查输入参数的格式和取值范围", "参考API文档中的参数说明"],
        )


class SystemError(BaseError):
    """
    系统级错误

    用于内部服务错误、配置错误、资源不足等系统问题
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
            suggestions=suggestions or ["稍后重试操作", "如问题持续存在，请联系系统管理员"],
        )


class NetworkError(BaseError):
    """
    网络通信错误

    用于HTTP请求失败、连接超时等网络相关错误
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
        # 扩展网络错误上下文
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
            suggestions=suggestions or ["检查网络连接", "确认目标服务是否正常运行", "稍后重试"],
        )


class DatabaseError(BaseError):
    """
    数据库错误

    用于数据库连接失败、查询错误、事务失败等数据库相关错误
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
        # 扩展数据库错误上下文
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
            suggestions=suggestions or ["检查数据库连接配置", "确认数据库服务正常运行", "验证SQL语句语法"],
        )


class AuthenticationError(BaseError):
    """
    认证错误

    用于身份验证失败、令牌过期等认证相关错误
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
            suggestions=suggestions or ["检查认证凭据", "重新登录获取有效令牌"],
        )


class AuthorizationError(BaseError):
    """
    授权错误

    用于权限不足、访问被拒绝等授权相关错误
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
        # 扩展授权错误上下文
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
            suggestions=suggestions or ["联系管理员获取所需权限", "确认用户角色配置"],
        )


class ExternalServiceError(BaseError):
    """
    外部服务错误

    用于第三方服务调用失败、API限流等外部服务相关错误
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
        # 扩展外部服务错误上下文
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
            suggestions=suggestions or ["检查外部服务状态", "确认API配额和限制", "稍后重试"],
        )


# 便捷的错误创建函数，遵循DRY原则


def create_validation_error(field_name: str, message: str, field_value: Any = None) -> ValidationError:
    """创建字段验证错误的便捷函数"""
    return ValidationError(
        message=f"Field '{field_name}' validation failed: {message}",
        field_name=field_name,
        field_value=field_value,
        error_code=ErrorCode.INVALID_FIELD_FORMAT,
    )


def create_business_error(message: str, context: Optional[Dict[str, Any]] = None) -> BusinessError:
    """创建业务错误的便捷函数"""
    return BusinessError(message=message, context=context, error_code=ErrorCode.BUSINESS_RULE_VIOLATION)


def create_system_error(message: str, cause: Optional[Exception] = None) -> SystemError:
    """创建系统错误的便捷函数"""
    return SystemError(
        message=message, cause=cause, error_code=ErrorCode.INTERNAL_SERVER_ERROR, severity=ErrorSeverity.HIGH
    )


def create_database_error(
    message: str, operation: str, table_name: Optional[str] = None, cause: Optional[Exception] = None
) -> DatabaseError:
    """创建数据库错误的便捷函数"""
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
    """创建网络错误的便捷函数"""
    return NetworkError(
        message=message, url=url, status_code=status_code, cause=cause, error_code=ErrorCode.CONNECTION_FAILED
    )
