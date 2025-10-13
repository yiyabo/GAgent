#!/usr/bin/env python3
"""
警告配置管理模块

统一管理项目中的警告过滤和处理，确保：
1. 重要警告不被忽略
2. 无害警告不干扰开发
3. 已知问题有清晰的追踪
"""

import functools
import logging
import warnings
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def configure_warnings():
    """配置项目级别的警告处理"""

    # 1. 重置警告过滤器
    warnings.resetwarnings()

    # 2. 设置默认警告行为
    warnings.simplefilter("default")

    # 3. 忽略已知的第三方库无害警告
    third_party_ignores = [
        # setuptools和pkg_resources的弃用警告
        ("ignore", None, DeprecationWarning, "pkg_resources.*"),
        ("ignore", None, DeprecationWarning, "setuptools.*"),
        # SQLite的无害警告
        ("ignore", None, DeprecationWarning, "sqlite3.*"),
        # asyncio的pending弃用警告
        ("ignore", None, PendingDeprecationWarning, "asyncio.*"),
        # 数值计算库的用户警告
        ("ignore", None, UserWarning, "numpy.*"),
        ("ignore", None, UserWarning, "matplotlib.*"),
        # 资源警告（通常是未关闭的文件等，在测试中很常见）
        ("ignore", None, ResourceWarning, None),
    ]

    for action, message, category, module in third_party_ignores:
        warnings.filterwarnings(action, message=message, category=category, module=module)

    # 4. 确保我们自己的弃用警告始终显示
    warnings.filterwarnings("always", category=DeprecationWarning, module="app.*")
    warnings.filterwarnings("always", category=DeprecationWarning, module="tests.*")

    logger.info("Warning filters configured")


def suppress_warnings(*categories):
    """装饰器：临时抑制特定类型的警告"""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with warnings.catch_warnings():
                for category in categories:
                    warnings.simplefilter("ignore", category)
                return func(*args, **kwargs)

        return wrapper

    return decorator


def track_warnings(func):
    """装饰器：跟踪函数执行期间的警告"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with warnings.catch_warnings(record=True) as warning_list:
            warnings.simplefilter("always")  # 捕获所有警告

            result = func(*args, **kwargs)

            # 记录警告信息
            if warning_list:
                logger.warning(f"Function {func.__name__} generated {len(warning_list)} warnings:")
                for w in warning_list:
                    logger.warning(f"  {w.category.__name__}: {w.message} ({w.filename}:{w.lineno})")

            return result

    return wrapper


class WarningContext:
    """警告上下文管理器"""

    def __init__(self, action: str = "ignore", category: type = Warning, message: str = "", module: str = ""):
        self.action = action
        self.category = category
        self.message = message
        self.module = module

    def __enter__(self):
        self.warnings_context = warnings.catch_warnings()
        self.warnings_context.__enter__()
        warnings.filterwarnings(self.action, message=self.message, category=self.category, module=self.module)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.warnings_context.__exit__(exc_type, exc_val, exc_tb)


def get_warning_stats() -> Dict[str, Any]:
    """获取当前警告配置统计"""
    filters = warnings.filters

    stats = {
        "total_filters": len(filters),
        "filter_actions": {},
        "filter_categories": {},
    }

    for filter_spec in filters:
        action = filter_spec[0]
        category = filter_spec[2]

        # 统计动作类型
        stats["filter_actions"][action] = stats["filter_actions"].get(action, 0) + 1

        # 统计警告类别
        if category:
            category_name = category.__name__
            stats["filter_categories"][category_name] = stats["filter_categories"].get(category_name, 0) + 1

    return stats


# 在模块导入时自动配置警告
configure_warnings()
