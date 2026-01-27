"""
Memory Session 隔离测试

测试记忆分库隔离功能：
- Session 专属数据库创建
- 跨 Session 数据隔离
- PRAGMA 配置验证
- 清理机制
"""

import os
import sqlite3
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from app.config.database_config import DatabaseConfig, get_database_config


class TestDatabaseConfig:
    """DatabaseConfig Session 相关功能测试"""

    @pytest.fixture
    def temp_db_root(self):
        """创建临时数据库目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def config(self, temp_db_root):
        """创建测试用配置"""
        with patch.dict(os.environ, {"DB_ROOT": temp_db_root}):
            cfg = DatabaseConfig()
            yield cfg

    def test_get_session_db_path(self, config):
        """测试 session 数据库路径生成"""
        path = config.get_session_db_path("test_session_123")

        assert isinstance(path, Path)
        assert "session_test_session_123.sqlite" in str(path)
        assert "sessions" in str(path)

    def test_sanitize_session_id(self, config):
        """测试 session_id 清理"""
        # 正常 ID
        assert config._sanitize_session_id("abc123") == "abc123"

        # 包含特殊字符
        assert config._sanitize_session_id("abc/123") == "abc_123"
        assert config._sanitize_session_id("abc\\123") == "abc_123"
        assert config._sanitize_session_id("abc:123") == "abc_123"

        # 过长 ID
        long_id = "a" * 100
        sanitized = config._sanitize_session_id(long_id)
        assert len(sanitized) <= 64

    def test_list_session_databases(self, config):
        """测试列出 session 数据库"""
        session_dir = config.get_session_db_dir()
        session_dir.mkdir(parents=True, exist_ok=True)

        # 创建测试文件
        (session_dir / "session_test1.sqlite").touch()
        (session_dir / "session_test2.sqlite").touch()
        (session_dir / "other_file.db").touch()  # 不应该被列出

        sessions = config.list_session_databases()

        assert len(sessions) == 2
        assert all("session_" in str(s) for s in sessions)

    def test_cleanup_old_sessions(self, config):
        """测试清理过期 session"""
        import time

        session_dir = config.get_session_db_dir()
        session_dir.mkdir(parents=True, exist_ok=True)

        # 创建一个"旧"文件（通过修改 mtime）
        old_file = session_dir / "session_old.sqlite"
        old_file.touch()

        # 将 mtime 设为 31 天前
        old_time = time.time() - (31 * 24 * 60 * 60)
        os.utime(old_file, (old_time, old_time))

        # 创建一个"新"文件
        new_file = session_dir / "session_new.sqlite"
        new_file.touch()

        # 清理 30 天前的文件
        cleaned = config.cleanup_old_sessions(max_age_days=30)

        assert cleaned == 1
        assert not old_file.exists()
        assert new_file.exists()


class TestMemoryServiceIsolation:
    """MemoryService Session 隔离测试"""

    @pytest.fixture
    def temp_db_root(self):
        """创建临时数据库目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_session_db_pragma_settings(self, temp_db_root):
        """测试 session 数据库的 PRAGMA 设置"""
        with patch.dict(os.environ, {"DB_ROOT": temp_db_root}):
            config = DatabaseConfig()
            session_path = config.get_session_db_path("pragma_test")
            session_path.parent.mkdir(parents=True, exist_ok=True)

            # 创建连接并设置 PRAGMA（模拟 _get_conn 行为）
            conn = sqlite3.connect(str(session_path), isolation_level="DEFERRED")
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")

            # 验证 PRAGMA 设置
            fk_result = conn.execute("PRAGMA foreign_keys").fetchone()
            assert fk_result[0] == 1, "foreign_keys should be ON"

            journal_result = conn.execute("PRAGMA journal_mode").fetchone()
            assert journal_result[0].lower() == "wal", "journal_mode should be WAL"

            timeout_result = conn.execute("PRAGMA busy_timeout").fetchone()
            assert timeout_result[0] == 5000, "busy_timeout should be 5000"

            conn.close()

    def test_session_creates_separate_database(self, temp_db_root):
        """测试不同 session 创建独立数据库"""
        with patch.dict(os.environ, {"DB_ROOT": temp_db_root}):
            config = DatabaseConfig()

            path1 = config.get_session_db_path("session_a")
            path2 = config.get_session_db_path("session_b")

            assert path1 != path2
            assert "session_a" in str(path1)
            assert "session_b" in str(path2)

    def test_foreign_key_cascade(self, temp_db_root):
        """测试外键级联删除"""
        with patch.dict(os.environ, {"DB_ROOT": temp_db_root}):
            config = DatabaseConfig()
            session_path = config.get_session_db_path("fk_test")
            session_path.parent.mkdir(parents=True, exist_ok=True)

            conn = sqlite3.connect(str(session_path))
            conn.execute("PRAGMA foreign_keys=ON")

            # 创建表（模拟记忆表结构）
            conn.execute("""
                CREATE TABLE memories (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE memory_embeddings (
                    memory_id TEXT PRIMARY KEY,
                    embedding_vector TEXT NOT NULL,
                    FOREIGN KEY (memory_id) REFERENCES memories (id) ON DELETE CASCADE
                )
            """)

            # 插入数据
            conn.execute("INSERT INTO memories VALUES ('mem1', 'test content')")
            conn.execute("INSERT INTO memory_embeddings VALUES ('mem1', '[0.1, 0.2, 0.3]')")
            conn.commit()

            # 验证数据存在
            assert conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0] == 1

            # 删除父记录
            conn.execute("DELETE FROM memories WHERE id = 'mem1'")
            conn.commit()

            # 验证级联删除
            count = conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0]
            assert count == 0, "Embedding should be cascaded deleted"

            conn.close()


class TestSessionIdValidation:
    """Session ID 验证测试"""

    def test_valid_session_ids(self):
        """测试有效的 session ID"""
        config = DatabaseConfig.__new__(DatabaseConfig)

        valid_ids = [
            "abc123",
            "user-session-1",
            "session_with_underscore",
            "MixedCase123",
        ]

        for session_id in valid_ids:
            sanitized = config._sanitize_session_id(session_id)
            assert sanitized == session_id, f"Valid ID should not change: {session_id}"

    def test_invalid_characters_replaced(self):
        """测试无效字符被替换"""
        config = DatabaseConfig.__new__(DatabaseConfig)

        test_cases = [
            ("path/to/session", "path_to_session"),
            ("session:with:colons", "session_with_colons"),
            ("session<script>", "session_script_"),
            ("session with spaces", "session_with_spaces"),
        ]

        for original, expected in test_cases:
            sanitized = config._sanitize_session_id(original)
            assert sanitized == expected, f"Expected {expected}, got {sanitized}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
