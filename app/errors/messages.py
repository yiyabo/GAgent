"""
Internationalized Error Message System

Supports multi-language error messages with error classification and error code system
Following YAGNI principle: Currently implemented with English as default, framework supports future extension
"""

from enum import Enum
from typing import Any, Dict, Optional

from .exceptions import ErrorCode


class Language(Enum):
    """Supported languages"""

    ZH_CN = "zh_cn"  # Simplified Chinese
    EN_US = "en_us"  # American English


class ErrorMessageRegistry:
    """
    Error Message Registry

    Follows Single Responsibility Principle: Dedicated to managing error messages
    Follows Open-Closed Principle: Extensible for new languages and error types
    """

    def __init__(self):
        self._messages = {}
        self._default_language = Language.EN_US
        self._init_messages()

    def _init_messages(self):
        """Initialize error messages"""
        self._messages = {
            # Business error messages (1000-1999)
            ErrorCode.BUSINESS_RULE_VIOLATION: {
                Language.ZH_CN: {
                    "message": "",
                    "description": "",
                    "suggestions": ["", "confirm", "confirm"],
                },
                Language.EN_US: {
                    "message": "Business rule violation",
                    "description": "Operation violates business logic rules",
                    "suggestions": ["Check business rules", "Verify operation preconditions", "Contact business team"],
                },
            },
            ErrorCode.TASK_NOT_FOUND: {
                Language.ZH_CN: {
                    "message": "taskdoes not exist",
                    "description": "task id",
                    "suggestions": ["check task id", "confirm task was not deleted", "refresh task list"],
                },
                Language.EN_US: {
                    "message": "Task not found",
                    "description": "The specified task ID was not found",
                    "suggestions": ["Check if task ID is correct", "Verify task was not deleted", "Refresh task list"],
                },
            },
            ErrorCode.INVALID_TASK_STATE: {
                Language.ZH_CN: {
                    "message": "invalid task state",
                    "description": "task status does not allow this operation",
                    "suggestions": ["check task status", "verify operation timing", "wait for status change"],
                },
                Language.EN_US: {
                    "message": "Invalid task state",
                    "description": "Current task state does not allow this operation",
                    "suggestions": ["Check current task state", "Verify operation timing", "Wait for state change"],
                },
            },
            ErrorCode.INSUFFICIENT_RESOURCES: {
                Language.ZH_CN: {
                    "message": "",
                    "description": "systemcompleted",
                    "suggestions": ["", "system", ""],
                },
                Language.EN_US: {
                    "message": "Insufficient resources",
                    "description": "System resources insufficient to complete operation",
                    "suggestions": ["Free up resources", "Increase system quota", "Retry later"],
                },
            },
            ErrorCode.GOAL_VALIDATION_FAILED: {
                Language.ZH_CN: {
                    "message": "failed",
                    "description": "",
                    "suggestions": ["", "content", ""],
                },
                Language.EN_US: {
                    "message": "Goal validation failed",
                    "description": "Provided goal does not meet validation requirements",
                    "suggestions": [
                        "Check goal format",
                        "Ensure complete goal content",
                        "Reference goal setting guide",
                    ],
                },
            },
            # Validation error messages (2000-2999)
            ErrorCode.MISSING_REQUIRED_FIELD: {
                Language.ZH_CN: {
                    "message": "",
                    "description": "missing required field",
                    "suggestions": ["", "API", "parameter"],
                },
                Language.EN_US: {
                    "message": "Missing required field",
                    "description": "Required field is missing from request",
                    "suggestions": [
                        "Check all required fields",
                        "Reference API documentation",
                        "Add missing parameters",
                    ],
                },
            },
            ErrorCode.INVALID_FIELD_FORMAT: {
                Language.ZH_CN: {
                    "message": "",
                    "description": "",
                    "suggestions": ["", "type", ""],
                },
                Language.EN_US: {
                    "message": "Invalid field format",
                    "description": "Field value does not match expected format",
                    "suggestions": [
                        "Check field format requirements",
                        "Use correct data type",
                        "Reference format examples",
                    ],
                },
            },
            ErrorCode.FIELD_VALUE_OUT_OF_RANGE: {
                Language.ZH_CN: {
                    "message": "",
                    "description": "",
                    "suggestions": ["", "parameter", ""],
                },
                Language.EN_US: {
                    "message": "Field value out of range",
                    "description": "Field value exceeds allowed range",
                    "suggestions": ["Check range limits", "Adjust parameter value", "Use reasonable values"],
                },
            },
            ErrorCode.INVALID_JSON_FORMAT: {
                Language.ZH_CN: {
                    "message": "JSON",
                    "description": "pleaseJSON",
                    "suggestions": ["JSON", "JSON", ""],
                },
                Language.EN_US: {
                    "message": "Invalid JSON format",
                    "description": "Request data is not valid JSON format",
                    "suggestions": [
                        "Check JSON syntax",
                        "Use JSON validation tool",
                        "Ensure proper quotes and brackets",
                    ],
                },
            },
            ErrorCode.SCHEMA_VALIDATION_FAILED: {
                Language.ZH_CN: {
                    "message": "failed",
                    "description": "",
                    "suggestions": ["", "", "model"],
                },
                Language.EN_US: {
                    "message": "Schema validation failed",
                    "description": "Data does not match expected structure definition",
                    "suggestions": ["Check data structure", "Reference schema documentation", "Use correct data model"],
                },
            },
            # System error messages (3000-3999)
            ErrorCode.INTERNAL_SERVER_ERROR: {
                Language.ZH_CN: {
                    "message": "serviceerror",
                    "description": "service internal error",
                    "suggestions": ["", "servicestatus", "support"],
                },
                Language.EN_US: {
                    "message": "Internal server error",
                    "description": "Internal error occurred while processing request",
                    "suggestions": ["Retry later", "Check server status", "Contact technical support"],
                },
            },
            ErrorCode.SERVICE_UNAVAILABLE: {
                Language.ZH_CN: {
                    "message": "serviceunavailable",
                    "description": "serviceunavailable",
                    "suggestions": ["", "servicestatus", "service"],
                },
                Language.EN_US: {
                    "message": "Service unavailable",
                    "description": "Service is temporarily unavailable",
                    "suggestions": ["Retry later", "Check service status", "Use backup service"],
                },
            },
            ErrorCode.TIMEOUT_ERROR: {
                Language.ZH_CN: {
                    "message": "",
                    "description": "execute",
                    "suggestions": ["", "", ""],
                },
                Language.EN_US: {
                    "message": "Operation timeout",
                    "description": "Operation execution time exceeded expected duration",
                    "suggestions": [
                        "Increase timeout duration",
                        "Optimize operation efficiency",
                        "Process data in batches",
                    ],
                },
            },
            ErrorCode.CONFIGURATION_ERROR: {
                Language.ZH_CN: {
                    "message": "configurationerror",
                    "description": "systemconfigurationerror",
                    "suggestions": ["configurationfile", "updateconfiguration", "service"],
                },
                Language.EN_US: {
                    "message": "Configuration error",
                    "description": "System configuration error or missing",
                    "suggestions": ["Check configuration file", "Update configuration", "Restart service"],
                },
            },
            ErrorCode.MEMORY_INSUFFICIENT: {
                Language.ZH_CN: {
                    "message": "",
                    "description": "systemavailable",
                    "suggestions": ["", "configuration", ""],
                },
                Language.EN_US: {
                    "message": "Insufficient memory",
                    "description": "Insufficient available system memory",
                    "suggestions": ["Free memory resources", "Increase memory configuration", "Optimize memory usage"],
                },
            },
            # Network error messages (4000-4999)
            ErrorCode.CONNECTION_FAILED: {
                Language.ZH_CN: {
                    "message": "connectionfailed",
                    "description": "networkconnection",
                    "suggestions": ["networkconnection", "confirm", ""],
                },
                Language.EN_US: {
                    "message": "Connection failed",
                    "description": "Unable to establish network connection",
                    "suggestions": ["Check network connectivity", "Verify target address", "Check firewall settings"],
                },
            },
            ErrorCode.REQUEST_TIMEOUT: {
                Language.ZH_CN: {
                    "message": "please",
                    "description": "networkplease",
                    "suggestions": ["network", "", "please"],
                },
                Language.EN_US: {
                    "message": "Request timeout",
                    "description": "Network request timed out",
                    "suggestions": ["Check network speed", "Increase timeout", "Retry request"],
                },
            },
            # Database error messages (5000-5999)
            ErrorCode.DATABASE_CONNECTION_FAILED: {
                Language.ZH_CN: {
                    "message": "databaseconnectionfailed",
                    "description": "connectiondatabaseservice",
                    "suggestions": ["databaseservicestatus", "connectionconfiguration", "network"],
                },
                Language.EN_US: {
                    "message": "Database connection failed",
                    "description": "Unable to connect to database server",
                    "suggestions": [
                        "Check database service status",
                        "Verify connection configuration",
                        "Check network connectivity",
                    ],
                },
            },
            ErrorCode.QUERY_EXECUTION_FAILED: {
                Language.ZH_CN: {
                    "message": "query execution failed",
                    "description": "databaseexecute",
                    "suggestions": ["SQL", "", ""],
                },
                Language.EN_US: {
                    "message": "Query execution failed",
                    "description": "Database query execution failed",
                    "suggestions": ["Check SQL syntax", "Verify table structure", "Check data permissions"],
                },
            },
            # Authentication/Authorization error messages (6000-6999)
            ErrorCode.AUTHENTICATION_FAILED: {
                Language.ZH_CN: {
                    "message": "failed",
                    "description": "",
                    "suggestions": ["", "confirmstatus", ""],
                },
                Language.EN_US: {
                    "message": "Authentication failed",
                    "description": "User authentication failed",
                    "suggestions": ["Check username and password", "Verify account status", "Login again"],
                },
            },
            ErrorCode.TOKEN_EXPIRED: {
                Language.ZH_CN: {
                    "message": "",
                    "description": "refresh",
                    "suggestions": ["refresh", "", ""],
                },
                Language.EN_US: {
                    "message": "Token expired",
                    "description": "Access token has expired and needs refresh",
                    "suggestions": ["Refresh access token", "Login again", "Check token validity period"],
                },
            },
            ErrorCode.INSUFFICIENT_PERMISSIONS: {
                Language.ZH_CN: {
                    "message": "",
                    "description": "execute",
                    "suggestions": ["get", "confirm", "configuration"],
                },
                Language.EN_US: {
                    "message": "Insufficient permissions",
                    "description": "User lacks sufficient permissions for this operation",
                    "suggestions": [
                        "Contact admin for permissions",
                        "Verify user role",
                        "Check permission configuration",
                    ],
                },
            },
            # External service error messages (7000-7999)
            ErrorCode.LLM_SERVICE_ERROR: {
                Language.ZH_CN: {
                    "message": "LLMserviceerror",
                    "description": "modelservicefailed",
                    "suggestions": ["LLMservicestatus", "confirmAPI", ""],
                },
                Language.EN_US: {
                    "message": "LLM service error",
                    "description": "Large Language Model service call failed",
                    "suggestions": ["Check LLM service status", "Verify API key validity", "Retry later"],
                },
            },
            ErrorCode.EMBEDDING_SERVICE_ERROR: {
                Language.ZH_CN: {
                    "message": "serviceerror",
                    "description": "servicefailed",
                    "suggestions": ["servicestatus", "confirmmodelavailable", ""],
                },
                Language.EN_US: {
                    "message": "Embedding service error",
                    "description": "Vectorization service call failed",
                    "suggestions": ["Check vector service status", "Verify model availability", "Regenerate vectors"],
                },
            },
            ErrorCode.MCP_SERVICE_ERROR: {
                Language.ZH_CN: {
                    "message": "MCPserviceerror",
                    "description": "Memory-MCPservicefailed",
                    "suggestions": ["MCPservicestatus", "serviceconfiguration", "MCPservice"],
                },
                Language.EN_US: {
                    "message": "MCP service error",
                    "description": "Memory-MCP service call failed",
                    "suggestions": ["Check MCP service status", "Verify memory service config", "Restart MCP service"],
                },
            },
            ErrorCode.API_RATE_LIMIT_EXCEEDED: {
                Language.ZH_CN: {
                    "message": "API",
                    "description": "API",
                    "suggestions": ["reduce request rate", "request higher quota", "retry with backoff"],
                },
                Language.EN_US: {
                    "message": "API rate limit exceeded",
                    "description": "API call frequency exceeds limit",
                    "suggestions": ["Reduce call frequency", "Request higher quota", "Use batch APIs"],
                },
            },
        }
        # Keep runtime outputs English-only after repository-wide language normalization.
        for error_data in self._messages.values():
            english_payload = error_data.get(Language.EN_US)
            if english_payload:
                error_data[Language.ZH_CN] = self._clone_message_payload(english_payload)

    @staticmethod
    def _clone_message_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Clone message payload to avoid shared mutable list references."""
        return {
            "message": str(payload.get("message", "")),
            "description": str(payload.get("description", "")),
            "suggestions": list(payload.get("suggestions", [])),
        }

    def get_message(
        self, error_code: int, language: Optional[Language] = None, context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Get error message

        Args:
            error_code: Error code
            language: Language, defaults to system default language
            context: Context information for message template substitution

        Returns:
            Error message dict containing message, description, suggestions
        """
        lang = language or self._default_language

        if error_code not in self._messages:
            # Always use English for consistent output
            language = Language.EN_US
            # Return default error message
            return {
                "message": "Unknown error",
                "description": f"Error code {error_code} not defined",
                "suggestions": ["Contact technical support"],
            }

        error_data = self._messages[error_code]

        if lang not in error_data:
            # Fallback to default language
            lang = self._default_language

        message_data = error_data[lang].copy()

        # Support message template substitution (future extension)
        if context:
            message_data = self._format_with_context(message_data, context)

        return message_data

    def _format_with_context(self, message_data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Format message with context information (reserved extension interface)

        Args:
            message_data: Original message data
            context: Context information

        Returns:
            Formatted message data
        """
        # Simple string replacement implementation
        formatted_data = {}
        for key, value in message_data.items():
            if isinstance(value, str):
                try:
                    formatted_data[key] = value.format(**context)
                except (KeyError, ValueError):
                    formatted_data[key] = value
            elif isinstance(value, list):
                formatted_data[key] = [item.format(**context) if isinstance(item, str) else item for item in value]
            else:
                formatted_data[key] = value

        return formatted_data

    def set_default_language(self, language: Language):
        """Set default language"""
        self._default_language = language

    def add_custom_message(self, error_code: int, language: Language, message_data: Dict[str, Any]):
        """
        Add custom error message

        Args:
            error_code: Error code
            language: Language
            message_data: Message data containing message, description, suggestions
        """
        if error_code not in self._messages:
            self._messages[error_code] = {}

        self._messages[error_code][language] = message_data


# Global error message registry instance
message_registry = ErrorMessageRegistry()


def get_error_message(
    error_code: int, language: Optional[Language] = None, context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Convenience function to get error message"""
    return message_registry.get_message(error_code, language, context)


def set_default_language(language: Language):
    """Convenience function to set default language"""
    message_registry.set_default_language(language)
