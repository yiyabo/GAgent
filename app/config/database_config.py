"""Database path configuration utilities."""

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class DatabaseConfig:
    """Centralized database path manager."""

    def __init__(self):
        self.db_root = os.getenv("DB_ROOT", "data/databases")
        self.ensure_db_directory()

    def ensure_db_directory(self):
        """Ensure all database directories exist."""
        Path(self.db_root).mkdir(parents=True, exist_ok=True)

        subdirs = ["main", "cache", "temp", "backups", "plans", "jobs", "sessions"]
        for subdir in subdirs:
            Path(self.db_root, subdir).mkdir(parents=True, exist_ok=True)

    def get_main_db_path(self) -> str:
        """Return the main plan registry database path."""
        return os.path.join(self.db_root, "main", "plan_registry.db")

    def get_cache_db_path(self, cache_type: str) -> str:
        """Return cache database path for a cache namespace."""
        return os.path.join(self.db_root, "cache", f"{cache_type}_cache.db")

    def get_temp_db_path(self, name: str) -> str:
        """Return temporary database path."""
        return os.path.join(self.db_root, "temp", f"{name}.db")

    def get_backup_db_path(self, name: str, timestamp: Optional[str] = None) -> str:
        """Return backup database path."""
        if timestamp:
            filename = f"{name}_{timestamp}.db"
        else:
            filename = f"{name}_backup.db"
        return os.path.join(self.db_root, "backups", filename)

    def get_plan_store_dir(self) -> Path:
        """Return directory for PlanTree database files."""
        return Path(self.db_root, "plans")

    def get_system_jobs_db_path(self) -> Path:
        """Return database path for system-wide jobs."""
        return Path(self.db_root, "jobs", "system_jobs.sqlite")

    def get_session_db_dir(self) -> Path:
        """Return directory for per-session memory databases."""
        return Path(self.db_root, "sessions")

    def get_session_db_path(self, session_id: str) -> Path:
        """
        Return database path for a specific session.

        Args:
            session_id: Session identifier.

        Returns:
            Session-scoped database file path.
        """
        safe_id = self._sanitize_session_id(session_id)
        return self.get_session_db_dir() / f"session_{safe_id}.sqlite"

    def _sanitize_session_id(self, session_id: str) -> str:
        """
        Sanitize session ID for filesystem-safe usage.

        Args:
            session_id: Raw session identifier.

        Returns:
            Safe filename-friendly string.
        """
        import re
        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", session_id)
        return safe_id[:64] if len(safe_id) > 64 else safe_id

    def list_session_databases(self) -> list:
        """
        List all session database files.

        Returns:
            List of session database file paths.
        """
        session_dir = self.get_session_db_dir()
        if not session_dir.exists():
            return []
        return list(session_dir.glob("session_*.sqlite"))

    def cleanup_old_sessions(self, max_age_days: int = 30) -> int:
        """
        Delete expired session databases.

        Args:
            max_age_days: Maximum retention period in days.

        Returns:
            Number of deleted files.
        """
        import time
        from datetime import datetime, timedelta

        cutoff_time = time.time() - (max_age_days * 24 * 60 * 60)
        cleaned = 0

        for db_path in self.list_session_databases():
            try:
                wal_path = db_path.with_suffix(".sqlite-wal")
                shm_path = db_path.with_suffix(".sqlite-shm")

                latest_mtime = db_path.stat().st_mtime
                if wal_path.exists():
                    latest_mtime = max(latest_mtime, wal_path.stat().st_mtime)
                if shm_path.exists():
                    latest_mtime = max(latest_mtime, shm_path.stat().st_mtime)

                if latest_mtime < cutoff_time:
                    db_path.unlink()
                    if wal_path.exists():
                        wal_path.unlink()
                    if shm_path.exists():
                        shm_path.unlink()
                    cleaned += 1
                    logger.info(f"Cleaned expired session database: {db_path.name}")
            except Exception as e:
                logger.warning(f"Failed to clean session database {db_path}: {e}")

        return cleaned

    def migrate_existing_databases(self):
        """Migrate legacy database files into the new directory structure."""
        import glob
        import shutil
        from datetime import datetime

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
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup_path = self.get_backup_db_path(
                        os.path.splitext(os.path.basename(old_path))[0], timestamp
                    )
                    shutil.copy2(old_path, backup_path)

                    shutil.move(old_path, new_path)
                    migrated.append(f"{old_path} -> {new_path}")

                    base_name = os.path.splitext(old_path)[0]
                    for ext in ["-wal", "-shm"]:
                        wal_shm_file = f"{old_path}{ext}"
                        if os.path.exists(wal_shm_file):
                            try:
                                os.remove(wal_shm_file)
                                cleaned.append(wal_shm_file)
                            except Exception as e:
                                logger.warning(f"Failed to delete {wal_shm_file}: {e}")

                except Exception as e:
                    logger.error(f"Migration failed for {old_path}: {e}")

        for pattern in ["*.db-wal", "*.db-shm"]:
            for wal_file in glob.glob(pattern):
                try:
                    os.remove(wal_file)
                    cleaned.append(wal_file)
                except Exception as e:
                    logger.warning(f"Failed to delete {wal_file}: {e}")

        if migrated:
            logger.info("Database migration completed:")
            for migration in migrated:
                logger.info(f"  📁 {migration}")

        if cleaned:
            logger.info("SQLite temporary file cleanup completed:")
            for cleaned_file in cleaned:
                logger.info(f"  🗑️  {cleaned_file}")

        if not migrated and not cleaned:
            logger.info("No files required migration or cleanup.")

    def get_database_info(self) -> dict:
        """Return current database configuration details."""
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
                "sessions": str(self.get_session_db_dir()),
            },
        }


_db_config: Optional[DatabaseConfig] = None


def get_database_config() -> DatabaseConfig:
    """Get singleton database configuration object."""
    global _db_config
    if _db_config is None:
        _db_config = DatabaseConfig()
    return _db_config


def get_main_database_path() -> str:
    """Convenience helper for main database path."""
    return get_database_config().get_main_db_path()


def get_cache_database_path(cache_type: str = "embedding") -> str:
    """Convenience helper for cache database path."""
    return get_database_config().get_cache_db_path(cache_type)
