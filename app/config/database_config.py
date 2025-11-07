"""
数据库配置管理

统一管理所有数据库文件的路径和配置。
"""

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class DatabaseConfig:
    """数据库配置管理类"""

    def __init__(self):
        # 数据库存储根目录
        self.db_root = os.getenv("DB_ROOT", "data/databases")
        self.ensure_db_directory()

    def ensure_db_directory(self):
        """确保数据库目录存在"""
        Path(self.db_root).mkdir(parents=True, exist_ok=True)

        # 创建子目录
        subdirs = ["main", "cache", "temp", "backups", "plans", "jobs"]
        for subdir in subdirs:
            Path(self.db_root, subdir).mkdir(parents=True, exist_ok=True)

    def get_main_db_path(self) -> str:
        """获取主数据库路径"""
        return os.path.join(self.db_root, "main", "plan_registry.db")

    def get_cache_db_path(self, cache_type: str) -> str:
        """获取缓存数据库路径"""
        return os.path.join(self.db_root, "cache", f"{cache_type}_cache.db")

    def get_temp_db_path(self, name: str) -> str:
        """获取临时数据库路径"""
        return os.path.join(self.db_root, "temp", f"{name}.db")

    def get_backup_db_path(self, name: str, timestamp: Optional[str] = None) -> str:
        """获取备份数据库路径"""
        if timestamp:
            filename = f"{name}_{timestamp}.db"
        else:
            filename = f"{name}_backup.db"
        return os.path.join(self.db_root, "backups", filename)

    def get_plan_store_dir(self) -> Path:
        """返回 Plan 独立数据库文件的目录."""
        return Path(self.db_root, "plans")

    def get_system_jobs_db_path(self) -> Path:
        """返回存放未绑定计划 Job 的数据库路径."""
        return Path(self.db_root, "jobs", "system_jobs.sqlite")

    def migrate_existing_databases(self):
        """迁移现有数据库文件到新结构"""
        import glob
        import shutil
        from datetime import datetime

        # 要迁移的数据库文件映射
        migrations = {
            "tasks.db": self.get_main_db_path(),
            "embedding_cache.db": self.get_cache_db_path("embedding"),
            "llm_cache.db": self.get_cache_db_path("llm"),
            "statistics.db": self.get_temp_db_path("statistics"),
            "medical_ai.db": self.get_temp_db_path("medical_ai"),
            "drug_research.db": self.get_temp_db_path("drug_research"),
        }

        migrated = []
        cleaned = []

        for old_path, new_path in migrations.items():
            if os.path.exists(old_path):
                try:
                    # 备份原文件
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup_path = self.get_backup_db_path(
                        os.path.splitext(os.path.basename(old_path))[0], timestamp
                    )
                    shutil.copy2(old_path, backup_path)

                    # 移动到新位置
                    shutil.move(old_path, new_path)
                    migrated.append(f"{old_path} -> {new_path}")

                    # 处理相关的WAL和SHM文件
                    base_name = os.path.splitext(old_path)[0]
                    for ext in ["-wal", "-shm"]:
                        wal_shm_file = f"{old_path}{ext}"
                        if os.path.exists(wal_shm_file):
                            try:
                                os.remove(wal_shm_file)
                                cleaned.append(wal_shm_file)
                            except Exception as e:
                                logger.warning(f"无法删除 {wal_shm_file}: {e}")

                except Exception as e:
                    logger.error(f"迁移失败 {old_path}: {e}")

        # 清理剩余的WAL/SHM文件
        for pattern in ["*.db-wal", "*.db-shm"]:
            for wal_file in glob.glob(pattern):
                try:
                    os.remove(wal_file)
                    cleaned.append(wal_file)
                except Exception as e:
                    logger.warning(f"无法删除 {wal_file}: {e}")

        if migrated:
            logger.info("数据库迁移完成:")
            for migration in migrated:
                logger.info(f"   📁 {migration}")

        if cleaned:
            logger.info("SQLite临时文件清理完成:")
            for cleaned_file in cleaned:
                logger.info(f"   🗑️  {cleaned_file}")

        if not migrated and not cleaned:
            logger.info("没有需要迁移或清理的文件")

    def get_database_info(self) -> dict:
        """获取数据库配置信息"""
        return {
            "db_root": self.db_root,
            "main_db": self.get_main_db_path(),
            "cache_dbs": {
                "embedding": self.get_cache_db_path("embedding"),
                "llm": self.get_cache_db_path("llm"),
            },
            "directories": {
                "main": os.path.join(self.db_root, "main"),
                "cache": os.path.join(self.db_root, "cache"),
                "temp": os.path.join(self.db_root, "temp"),
                "backups": os.path.join(self.db_root, "backups"),
                "plans": str(self.get_plan_store_dir()),
            },
        }


# 全局配置实例
_db_config: Optional[DatabaseConfig] = None


def get_database_config() -> DatabaseConfig:
    """获取数据库配置实例"""
    global _db_config
    if _db_config is None:
        _db_config = DatabaseConfig()
    return _db_config


def get_main_database_path() -> str:
    """获取主数据库路径（向后兼容）"""
    return get_database_config().get_main_db_path()


def get_cache_database_path(cache_type: str = "embedding") -> str:
    """获取缓存数据库路径（向后兼容）"""
    return get_database_config().get_cache_db_path(cache_type)
