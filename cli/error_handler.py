"""
CLI友好错误处理器

提供用户友好的错误消息和退出码管理
遵循KISS原则，简化用户体验
"""

import sys
import logging
from typing import Any, Dict, Optional
from dataclasses import dataclass
from enum import Enum

try:
    from ..app.errors import BaseError, ErrorSeverity, ErrorCategory
    from ..app.errors import handle_cli_error
    from ..app.errors import get_error_message, Language
except ImportError:
    # 当作为独立模块导入时的回退方案
    from app.errors import BaseError, ErrorSeverity, ErrorCategory
    from app.errors import handle_cli_error
    from app.errors import get_error_message, Language


class ExitCode(Enum):
    """CLI退出码定义"""
    SUCCESS = 0
    GENERAL_ERROR = 1
    MISUSE_OF_COMMAND = 2  # 命令使用错误
    PERMISSION_DENIED = 3
    FILE_NOT_FOUND = 4
    NETWORK_ERROR = 5
    DATABASE_ERROR = 6
    CONFIGURATION_ERROR = 7
    VALIDATION_ERROR = 8
    BUSINESS_LOGIC_ERROR = 9
    SYSTEM_ERROR = 10


@dataclass
class CLIErrorInfo:
    """CLI错误信息"""
    exit_code: int
    message: str
    suggestions: list
    show_help: bool = False
    verbose_info: Optional[str] = None


class CLIErrorHandler:
    """
    CLI错误处理器
    
    提供友好的错误消息和合适的退出码
    """
    
    def __init__(self, verbose: bool = False, chinese: bool = False):
        self.verbose = verbose
        self.language = Language.ZH_CN if chinese else Language.EN_US
        self.logger = logging.getLogger(__name__)
    
    def handle_error(self, error: Exception) -> CLIErrorInfo:
        """
        处理错误并返回CLI错误信息
        
        Args:
            error: 异常实例
            
        Returns:
            CLI错误信息
        """
        if isinstance(error, BaseError):
            return self._handle_custom_error(error)
        else:
            return self._handle_standard_error(error)
    
    def _handle_custom_error(self, error: BaseError) -> CLIErrorInfo:
        """处理自定义错误"""
        # 使用统一的CLI错误格式化器
        cli_message = handle_cli_error(error, verbose=self.verbose)
        
        # 获取国际化的错误消息
        message_data = get_error_message(error.error_code, self.language)
        
        # 确定退出码
        exit_code = self._determine_exit_code(error)
        
        # 构建用户友好的建议
        suggestions = self._build_user_friendly_suggestions(
            error, 
            message_data.get('suggestions', [])
        )
        
        return CLIErrorInfo(
            exit_code=exit_code,
            message=self._format_user_message(error, message_data),
            suggestions=suggestions,
            show_help=self._should_show_help(error),
            verbose_info=cli_message if self.verbose else None
        )
    
    def _handle_standard_error(self, error: Exception) -> CLIErrorInfo:
        """处理标准异常"""
        error_type = type(error).__name__
        error_message = str(error)
        
        # 根据异常类型确定退出码和消息
        if isinstance(error, KeyboardInterrupt):
            return CLIErrorInfo(
                exit_code=ExitCode.SUCCESS.value,
                message="操作已被用户中断" if self.language == Language.ZH_CN else "Operation interrupted by user",
                suggestions=[]
            )
        
        elif isinstance(error, FileNotFoundError):
            return CLIErrorInfo(
                exit_code=ExitCode.FILE_NOT_FOUND.value,
                message=f"文件不存在: {error_message}" if self.language == Language.ZH_CN else f"File not found: {error_message}",
                suggestions=[
                    "检查文件路径是否正确" if self.language == Language.ZH_CN else "Check if file path is correct",
                    "确认文件确实存在" if self.language == Language.ZH_CN else "Verify file actually exists"
                ]
            )
        
        elif isinstance(error, PermissionError):
            return CLIErrorInfo(
                exit_code=ExitCode.PERMISSION_DENIED.value,
                message=f"权限不足: {error_message}" if self.language == Language.ZH_CN else f"Permission denied: {error_message}",
                suggestions=[
                    "检查文件或目录权限" if self.language == Language.ZH_CN else "Check file or directory permissions",
                    "使用管理员权限运行" if self.language == Language.ZH_CN else "Run with administrator privileges"
                ]
            )
        
        elif isinstance(error, ValueError):
            return CLIErrorInfo(
                exit_code=ExitCode.VALIDATION_ERROR.value,
                message=f"参数值错误: {error_message}" if self.language == Language.ZH_CN else f"Invalid parameter value: {error_message}",
                suggestions=[
                    "检查命令行参数格式" if self.language == Language.ZH_CN else "Check command line parameter format",
                    "使用 --help 查看正确用法" if self.language == Language.ZH_CN else "Use --help to see correct usage"
                ],
                show_help=True
            )
        
        else:
            return CLIErrorInfo(
                exit_code=ExitCode.GENERAL_ERROR.value,
                message=f"未知错误 ({error_type}): {error_message}" if self.language == Language.ZH_CN else f"Unknown error ({error_type}): {error_message}",
                suggestions=[
                    "稍后重试" if self.language == Language.ZH_CN else "Retry later",
                    "如问题持续存在，请报告此错误" if self.language == Language.ZH_CN else "Report this error if problem persists"
                ],
                verbose_info=f"Exception details: {error_type} at {error.__traceback__}" if self.verbose else None
            )
    
    def _determine_exit_code(self, error: BaseError) -> int:
        """根据错误类型确定退出码"""
        category_exit_code_map = {
            ErrorCategory.VALIDATION: ExitCode.VALIDATION_ERROR.value,
            ErrorCategory.BUSINESS: ExitCode.BUSINESS_LOGIC_ERROR.value,
            ErrorCategory.SYSTEM: ExitCode.SYSTEM_ERROR.value,
            ErrorCategory.DATABASE: ExitCode.DATABASE_ERROR.value,
            ErrorCategory.NETWORK: ExitCode.NETWORK_ERROR.value,
            ErrorCategory.AUTHENTICATION: ExitCode.PERMISSION_DENIED.value,
            ErrorCategory.AUTHORIZATION: ExitCode.PERMISSION_DENIED.value,
            ErrorCategory.EXTERNAL_SERVICE: ExitCode.NETWORK_ERROR.value
        }
        
        return category_exit_code_map.get(error.category, ExitCode.GENERAL_ERROR.value)
    
    def _format_user_message(self, error: BaseError, message_data: Dict[str, Any]) -> str:
        """格式化用户友好的错误消息"""
        if self.language == Language.ZH_CN:
            return f"❌ {message_data.get('message', '未知错误')}"
        else:
            return f"❌ {message_data.get('message', 'Unknown error')}"
    
    def _build_user_friendly_suggestions(
        self, 
        error: BaseError, 
        base_suggestions: list
    ) -> list:
        """构建用户友好的建议"""
        suggestions = []
        
        # 添加基础建议
        suggestions.extend(base_suggestions)
        
        # 根据错误严重程度添加特定建议
        if error.severity == ErrorSeverity.CRITICAL:
            if self.language == Language.ZH_CN:
                suggestions.append("立即联系系统管理员")
            else:
                suggestions.append("Contact system administrator immediately")
        
        # 根据错误上下文添加建议
        if error.context.get('field_name'):
            field_name = error.context['field_name']
            if self.language == Language.ZH_CN:
                suggestions.append(f"检查参数 '{field_name}' 的值")
            else:
                suggestions.append(f"Check the value of parameter '{field_name}'")
        
        # 添加通用帮助建议
        if error.category == ErrorCategory.VALIDATION:
            if self.language == Language.ZH_CN:
                suggestions.append("使用 --help 查看参数说明")
            else:
                suggestions.append("Use --help to see parameter descriptions")
        
        return suggestions
    
    def _should_show_help(self, error: BaseError) -> bool:
        """判断是否应该显示帮助信息"""
        return error.category in [
            ErrorCategory.VALIDATION,
        ] or 'help' in str(error.message).lower()
    
    def print_error(self, error_info: CLIErrorInfo) -> None:
        """打印错误信息到控制台"""
        # 主错误消息
        print(error_info.message, file=sys.stderr)
        
        # 建议信息
        if error_info.suggestions:
            if self.language == Language.ZH_CN:
                print("\n💡 建议:", file=sys.stderr)
            else:
                print("\n💡 Suggestions:", file=sys.stderr)
            
            for suggestion in error_info.suggestions:
                print(f"   • {suggestion}", file=sys.stderr)
        
        # 帮助信息提示
        if error_info.show_help:
            if self.language == Language.ZH_CN:
                print(f"\n使用 --help 查看完整帮助信息", file=sys.stderr)
            else:
                print(f"\nUse --help for complete help information", file=sys.stderr)
        
        # 详细信息 (仅在verbose模式下)
        if error_info.verbose_info and self.verbose:
            if self.language == Language.ZH_CN:
                print(f"\n🔍 详细信息:", file=sys.stderr)
            else:
                print(f"\n🔍 Detailed information:", file=sys.stderr)
            print(error_info.verbose_info, file=sys.stderr)
        
        # 错误ID提示 (如果是自定义错误)
        if hasattr(error_info, 'error_id'):
            if self.language == Language.ZH_CN:
                print(f"\n错误ID: {error_info.error_id}", file=sys.stderr)
            else:
                print(f"\nError ID: {error_info.error_id}", file=sys.stderr)


def handle_cli_exception(
    error: Exception, 
    verbose: bool = False, 
    chinese: bool = True
) -> int:
    """
    处理CLI异常的便捷函数
    
    Args:
        error: 异常实例
        verbose: 是否显示详细信息
        chinese: 是否使用中文
        
    Returns:
        退出码
    """
    handler = CLIErrorHandler(verbose=verbose, chinese=chinese)
    error_info = handler.handle_error(error)
    handler.print_error(error_info)
    return error_info.exit_code


def wrap_cli_main(main_func):
    """
    CLI主函数包装器装饰器
    
    用于统一处理CLI应用的异常和退出码
    """
    def wrapper(*args, **kwargs):
        try:
            result = main_func(*args, **kwargs)
            return result if isinstance(result, int) else 0
        except KeyboardInterrupt:
            print("\n操作已中断", file=sys.stderr)
            return 0
        except Exception as e:
            return handle_cli_exception(e, verbose=True)  # CLI应用默认显示详细信息
    
    return wrapper


# CLI错误上下文管理器

class CLIErrorContext:
    """CLI错误上下文管理器"""
    
    def __init__(
        self, 
        operation_name: str, 
        verbose: bool = False, 
        chinese: bool = True
    ):
        self.operation_name = operation_name
        self.handler = CLIErrorHandler(verbose=verbose, chinese=chinese)
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_val:
            error_info = self.handler.handle_error(exc_val)
            
            # 添加操作上下文信息
            if self.handler.language == Language.ZH_CN:
                contextual_message = f"Error occurred while executing operation '{self.operation_name}':\n{error_info.message}"
            else:
                contextual_message = f"Error occurred while executing operation '{self.operation_name}':\n{error_info.message}"
            
            error_info.message = contextual_message
            self.handler.print_error(error_info)
            
            # 返回True表示异常已处理
            return True