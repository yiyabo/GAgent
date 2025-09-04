"""
国际化错误消息系统

支持中英文错误消息，实现错误分类和错误码体系
遵循YAGNI原则：现在只实现中文，框架支持未来扩展
"""

from enum import Enum
from typing import Any, Dict, Optional

from .exceptions import ErrorCode


class Language(Enum):
    """支持的语言"""

    ZH_CN = "zh_cn"  # 简体中文
    EN_US = "en_us"  # 美式英语


class ErrorMessageRegistry:
    """
    错误消息注册表

    遵循单一职责原则：专门管理错误消息
    遵循开闭原则：可扩展新的语言和错误类型
    """

    def __init__(self):
        self._messages = {}
        self._default_language = Language.EN_US
        self._init_messages()

    def _init_messages(self):
        """初始化错误消息"""
        self._messages = {
            # 业务错误消息 (1000-1999)
            ErrorCode.BUSINESS_RULE_VIOLATION: {
                Language.ZH_CN: {
                    "message": "业务规则违反",
                    "description": "操作违反了业务逻辑规则",
                    "suggestions": ["检查业务规则", "确认操作的前置条件", "联系业务团队确认规则"],
                },
                Language.EN_US: {
                    "message": "Business rule violation",
                    "description": "Operation violates business logic rules",
                    "suggestions": ["Check business rules", "Verify operation preconditions", "Contact business team"],
                },
            },
            ErrorCode.TASK_NOT_FOUND: {
                Language.ZH_CN: {
                    "message": "任务不存在",
                    "description": "指定的任务ID未找到",
                    "suggestions": ["检查任务ID是否正确", "确认任务是否已被删除", "刷新任务列表"],
                },
                Language.EN_US: {
                    "message": "Task not found",
                    "description": "The specified task ID was not found",
                    "suggestions": ["Check if task ID is correct", "Verify task was not deleted", "Refresh task list"],
                },
            },
            ErrorCode.INVALID_TASK_STATE: {
                Language.ZH_CN: {
                    "message": "任务状态无效",
                    "description": "当前任务状态不允许此操作",
                    "suggestions": ["检查任务当前状态", "确认操作时机", "等待任务状态变更"],
                },
                Language.EN_US: {
                    "message": "Invalid task state",
                    "description": "Current task state does not allow this operation",
                    "suggestions": ["Check current task state", "Verify operation timing", "Wait for state change"],
                },
            },
            ErrorCode.INSUFFICIENT_RESOURCES: {
                Language.ZH_CN: {
                    "message": "资源不足",
                    "description": "系统资源不足以完成操作",
                    "suggestions": ["释放部分资源", "增加系统资源配额", "稍后重试"],
                },
                Language.EN_US: {
                    "message": "Insufficient resources",
                    "description": "System resources insufficient to complete operation",
                    "suggestions": ["Free up resources", "Increase system quota", "Retry later"],
                },
            },
            ErrorCode.GOAL_VALIDATION_FAILED: {
                Language.ZH_CN: {
                    "message": "目标验证失败",
                    "description": "提供的目标不符合验证要求",
                    "suggestions": ["检查目标格式", "确保目标内容完整", "参考目标设定指南"],
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
            # 验证错误消息 (2000-2999)
            ErrorCode.MISSING_REQUIRED_FIELD: {
                Language.ZH_CN: {
                    "message": "缺少必填字段",
                    "description": "请求中缺少必需的字段",
                    "suggestions": ["检查所有必填字段", "参考API文档", "补充缺失的参数"],
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
                    "message": "字段格式无效",
                    "description": "字段值不符合预期格式",
                    "suggestions": ["检查字段格式要求", "使用正确的数据类型", "参考格式示例"],
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
                    "message": "字段值超出范围",
                    "description": "字段值超出了允许的取值范围",
                    "suggestions": ["检查取值范围限制", "调整参数值", "使用合理的数值"],
                },
                Language.EN_US: {
                    "message": "Field value out of range",
                    "description": "Field value exceeds allowed range",
                    "suggestions": ["Check range limits", "Adjust parameter value", "Use reasonable values"],
                },
            },
            ErrorCode.INVALID_JSON_FORMAT: {
                Language.ZH_CN: {
                    "message": "JSON格式无效",
                    "description": "请求数据不是有效的JSON格式",
                    "suggestions": ["检查JSON语法", "使用JSON验证工具", "确保引号和括号正确配对"],
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
                    "message": "数据结构验证失败",
                    "description": "数据不符合预期的结构定义",
                    "suggestions": ["检查数据结构", "参考结构定义文档", "使用正确的数据模型"],
                },
                Language.EN_US: {
                    "message": "Schema validation failed",
                    "description": "Data does not match expected structure definition",
                    "suggestions": ["Check data structure", "Reference schema documentation", "Use correct data model"],
                },
            },
            # 系统错误消息 (3000-3999)
            ErrorCode.INTERNAL_SERVER_ERROR: {
                Language.ZH_CN: {
                    "message": "内部服务器错误",
                    "description": "服务器处理请求时发生内部错误",
                    "suggestions": ["稍后重试", "检查服务器状态", "联系技术支持"],
                },
                Language.EN_US: {
                    "message": "Internal server error",
                    "description": "Internal error occurred while processing request",
                    "suggestions": ["Retry later", "Check server status", "Contact technical support"],
                },
            },
            ErrorCode.SERVICE_UNAVAILABLE: {
                Language.ZH_CN: {
                    "message": "服务不可用",
                    "description": "当前服务暂时不可用",
                    "suggestions": ["稍后重试", "检查服务状态", "使用备用服务"],
                },
                Language.EN_US: {
                    "message": "Service unavailable",
                    "description": "Service is temporarily unavailable",
                    "suggestions": ["Retry later", "Check service status", "Use backup service"],
                },
            },
            ErrorCode.TIMEOUT_ERROR: {
                Language.ZH_CN: {
                    "message": "操作超时",
                    "description": "操作执行时间超出预期",
                    "suggestions": ["增加超时时间", "优化操作效率", "分批处理数据"],
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
                    "message": "配置错误",
                    "description": "系统配置存在错误或缺失",
                    "suggestions": ["检查配置文件", "更新配置信息", "重启服务"],
                },
                Language.EN_US: {
                    "message": "Configuration error",
                    "description": "System configuration error or missing",
                    "suggestions": ["Check configuration file", "Update configuration", "Restart service"],
                },
            },
            ErrorCode.MEMORY_INSUFFICIENT: {
                Language.ZH_CN: {
                    "message": "内存不足",
                    "description": "系统可用内存不足",
                    "suggestions": ["释放内存资源", "增加内存配置", "优化内存使用"],
                },
                Language.EN_US: {
                    "message": "Insufficient memory",
                    "description": "Insufficient available system memory",
                    "suggestions": ["Free memory resources", "Increase memory configuration", "Optimize memory usage"],
                },
            },
            # 网络错误消息 (4000-4999)
            ErrorCode.CONNECTION_FAILED: {
                Language.ZH_CN: {
                    "message": "连接失败",
                    "description": "无法建立网络连接",
                    "suggestions": ["检查网络连接", "确认目标地址正确", "检查防火墙设置"],
                },
                Language.EN_US: {
                    "message": "Connection failed",
                    "description": "Unable to establish network connection",
                    "suggestions": ["Check network connectivity", "Verify target address", "Check firewall settings"],
                },
            },
            ErrorCode.REQUEST_TIMEOUT: {
                Language.ZH_CN: {
                    "message": "请求超时",
                    "description": "网络请求超时",
                    "suggestions": ["检查网络速度", "增加超时时间", "重试请求"],
                },
                Language.EN_US: {
                    "message": "Request timeout",
                    "description": "Network request timed out",
                    "suggestions": ["Check network speed", "Increase timeout", "Retry request"],
                },
            },
            # 数据库错误消息 (5000-5999)
            ErrorCode.DATABASE_CONNECTION_FAILED: {
                Language.ZH_CN: {
                    "message": "数据库连接失败",
                    "description": "无法连接到数据库服务器",
                    "suggestions": ["检查数据库服务状态", "验证连接配置", "检查网络连通性"],
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
                    "message": "查询执行失败",
                    "description": "数据库查询执行时出错",
                    "suggestions": ["检查SQL语法", "验证表结构", "检查数据权限"],
                },
                Language.EN_US: {
                    "message": "Query execution failed",
                    "description": "Database query execution failed",
                    "suggestions": ["Check SQL syntax", "Verify table structure", "Check data permissions"],
                },
            },
            # 认证授权错误消息 (6000-6999)
            ErrorCode.AUTHENTICATION_FAILED: {
                Language.ZH_CN: {
                    "message": "身份验证失败",
                    "description": "用户身份验证未通过",
                    "suggestions": ["检查用户名和密码", "确认账户状态", "重新登录"],
                },
                Language.EN_US: {
                    "message": "Authentication failed",
                    "description": "User authentication failed",
                    "suggestions": ["Check username and password", "Verify account status", "Login again"],
                },
            },
            ErrorCode.TOKEN_EXPIRED: {
                Language.ZH_CN: {
                    "message": "令牌已过期",
                    "description": "访问令牌已过期需要刷新",
                    "suggestions": ["刷新访问令牌", "重新登录", "检查令牌有效期"],
                },
                Language.EN_US: {
                    "message": "Token expired",
                    "description": "Access token has expired and needs refresh",
                    "suggestions": ["Refresh access token", "Login again", "Check token validity period"],
                },
            },
            ErrorCode.INSUFFICIENT_PERMISSIONS: {
                Language.ZH_CN: {
                    "message": "权限不足",
                    "description": "用户权限不足以执行此操作",
                    "suggestions": ["联系管理员获取权限", "确认用户角色", "检查权限配置"],
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
            # 外部服务错误消息 (7000-7999)
            ErrorCode.LLM_SERVICE_ERROR: {
                Language.ZH_CN: {
                    "message": "LLM服务错误",
                    "description": "大语言模型服务调用失败",
                    "suggestions": ["检查LLM服务状态", "确认API密钥有效", "稍后重试"],
                },
                Language.EN_US: {
                    "message": "LLM service error",
                    "description": "Large Language Model service call failed",
                    "suggestions": ["Check LLM service status", "Verify API key validity", "Retry later"],
                },
            },
            ErrorCode.EMBEDDING_SERVICE_ERROR: {
                Language.ZH_CN: {
                    "message": "嵌入向量服务错误",
                    "description": "向量化服务调用失败",
                    "suggestions": ["检查向量服务状态", "确认模型可用性", "重新生成向量"],
                },
                Language.EN_US: {
                    "message": "Embedding service error",
                    "description": "Vectorization service call failed",
                    "suggestions": ["Check vector service status", "Verify model availability", "Regenerate vectors"],
                },
            },
            ErrorCode.MCP_SERVICE_ERROR: {
                Language.ZH_CN: {
                    "message": "MCP服务错误",
                    "description": "Memory-MCP服务调用失败",
                    "suggestions": ["检查MCP服务状态", "验证内存服务配置", "重启MCP服务"],
                },
                Language.EN_US: {
                    "message": "MCP service error",
                    "description": "Memory-MCP service call failed",
                    "suggestions": ["Check MCP service status", "Verify memory service config", "Restart MCP service"],
                },
            },
            ErrorCode.API_RATE_LIMIT_EXCEEDED: {
                Language.ZH_CN: {
                    "message": "API调用频率超限",
                    "description": "API调用频率超过限制",
                    "suggestions": ["降低调用频率", "申请更高配额", "使用批量接口"],
                },
                Language.EN_US: {
                    "message": "API rate limit exceeded",
                    "description": "API call frequency exceeds limit",
                    "suggestions": ["Reduce call frequency", "Request higher quota", "Use batch APIs"],
                },
            },
        }

    def get_message(
        self, error_code: int, language: Optional[Language] = None, context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        获取错误消息

        Args:
            error_code: 错误码
            language: 语言，默认使用系统默认语言
            context: 上下文信息，用于消息模板替换

        Returns:
            错误消息字典，包含message、description、suggestions
        """
        lang = language or self._default_language

        if error_code not in self._messages:
            # Always use English for consistent output
            language = Language.EN_US
            # 返回默认错误消息
            return {
                "message": "Unknown error",
                "description": f"Error code {error_code} not defined",
                "suggestions": ["Contact technical support"],
            }

        error_data = self._messages[error_code]

        if lang not in error_data:
            # 回退到默认语言
            lang = self._default_language

        message_data = error_data[lang].copy()

        # 支持消息模板替换（未来扩展功能）
        if context:
            message_data = self._format_with_context(message_data, context)

        return message_data

    def _format_with_context(self, message_data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """
        使用上下文信息格式化消息（预留扩展接口）

        Args:
            message_data: 原始消息数据
            context: 上下文信息

        Returns:
            格式化后的消息数据
        """
        # 简单的字符串替换实现
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
        """设置默认语言"""
        self._default_language = language

    def add_custom_message(self, error_code: int, language: Language, message_data: Dict[str, Any]):
        """
        添加自定义错误消息

        Args:
            error_code: 错误码
            language: 语言
            message_data: 消息数据，包含message、description、suggestions
        """
        if error_code not in self._messages:
            self._messages[error_code] = {}

        self._messages[error_code][language] = message_data


# 全局错误消息注册表实例
message_registry = ErrorMessageRegistry()


def get_error_message(
    error_code: int, language: Optional[Language] = None, context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """获取错误消息的便捷函数"""
    return message_registry.get_message(error_code, language, context)


def set_default_language(language: Language):
    """设置默认语言的便捷函数"""
    message_registry.set_default_language(language)
