"""
配置管理包

统一管理系统的各种配置，包括数据库路径、缓存设置等。
"""

from .database_config import (
    DatabaseConfig,
    get_database_config,
    get_main_database_path,
    get_cache_database_path,
)

__all__ = [
    "DatabaseConfig",
    "get_database_config", 
    "get_main_database_path",
    "get_cache_database_path",
]
