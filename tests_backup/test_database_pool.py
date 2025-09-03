"""
Tests for database connection pool functionality.
"""

import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from app.database_pool import (
    SQLiteConnectionPool,
    close_connection_pool,
    get_connection_pool,
    get_db,
    get_pool_stats,
    initialize_connection_pool,
)


class TestSQLiteConnectionPool:

    def test_pool_initialization(self):
        """Test connection pool initialization."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            pool = SQLiteConnectionPool(db_path, pool_size=5, max_overflow=2)

            assert pool.pool_size == 5
            assert pool.max_overflow == 2
            assert pool.db_path == db_path

            stats = pool.get_stats()
            assert stats["pool_size"] == 5
            assert stats["available_connections"] == 5
            assert stats["overflow_connections"] == 0

            pool.close_pool()
        finally:
            os.unlink(db_path)

    def test_get_and_return_connection(self):
        """Test getting and returning connections."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            pool = SQLiteConnectionPool(db_path, pool_size=3)

            # Get connection
            conn = pool.get_connection()
            assert conn is not None

            # Pool should have one less connection
            stats = pool.get_stats()
            assert stats["available_connections"] == 2

            # Return connection
            pool.return_connection(conn)

            # Pool should be back to full
            stats = pool.get_stats()
            assert stats["available_connections"] == 3

            pool.close_pool()
        finally:
            os.unlink(db_path)

    def test_context_manager(self):
        """Test connection context manager."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            pool = SQLiteConnectionPool(db_path, pool_size=3)

            # Use context manager
            with pool.connection() as conn:
                # Connection should be available
                assert conn is not None

                # Create a test table
                conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT)")
                conn.execute("INSERT INTO test (name) VALUES ('test')")

                # Verify data
                result = conn.execute("SELECT COUNT(*) as count FROM test").fetchone()
                assert result["count"] == 1

            # After context manager, connection should be returned
            stats = pool.get_stats()
            assert stats["available_connections"] == 3

            pool.close_pool()
        finally:
            os.unlink(db_path)

    def test_overflow_connections(self):
        """Test overflow connection handling."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            pool = SQLiteConnectionPool(db_path, pool_size=2, max_overflow=1)

            # Get all regular connections
            conn1 = pool.get_connection()
            conn2 = pool.get_connection()

            stats = pool.get_stats()
            assert stats["available_connections"] == 0
            assert stats["overflow_connections"] == 0

            # Get overflow connection
            conn3 = pool.get_connection()

            stats = pool.get_stats()
            assert stats["available_connections"] == 0
            assert stats["overflow_connections"] == 1

            # Return connections
            pool.return_connection(conn1)
            pool.return_connection(conn2)
            pool.return_connection(conn3, is_overflow=True)

            stats = pool.get_stats()
            assert stats["available_connections"] == 2
            assert stats["overflow_connections"] == 0

            pool.close_pool()
        finally:
            os.unlink(db_path)

    def test_concurrent_access(self):
        """Test concurrent access to connection pool."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            pool = SQLiteConnectionPool(db_path, pool_size=5, max_overflow=3)

            # Create test table
            with pool.connection() as conn:
                conn.execute("CREATE TABLE concurrent_test (id INTEGER PRIMARY KEY, thread_id TEXT)")

            results = []
            errors = []

            def worker(thread_id):
                """Worker function for concurrent testing."""
                try:
                    with pool.connection() as conn:
                        # Insert data
                        conn.execute("INSERT INTO concurrent_test (thread_id) VALUES (?)", (f"thread_{thread_id}",))

                        # Read data back
                        result = conn.execute(
                            "SELECT COUNT(*) as count FROM concurrent_test WHERE thread_id = ?",
                            (f"thread_{thread_id}",),
                        ).fetchone()

                        results.append((thread_id, result["count"]))

                except Exception as e:
                    errors.append((thread_id, str(e)))

            # Run concurrent operations
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(worker, i) for i in range(10)]
                for future in as_completed(futures):
                    future.result()  # Wait for completion

            # Check results
            assert len(errors) == 0, f"Concurrent operations failed: {errors}"
            assert len(results) == 10

            # Verify all data was inserted
            with pool.connection() as conn:
                total = conn.execute("SELECT COUNT(*) as count FROM concurrent_test").fetchone()
                assert total["count"] == 10

            pool.close_pool()
        finally:
            os.unlink(db_path)

    def test_connection_timeout(self):
        """Test connection timeout handling."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            pool = SQLiteConnectionPool(db_path, pool_size=1, max_overflow=0, timeout=0.1)

            # Get the only connection
            conn = pool.get_connection()

            # Try to get another connection - should timeout
            with pytest.raises(TimeoutError):
                pool.get_connection()

            # Return connection
            pool.return_connection(conn)

            # Now should be able to get connection again
            conn2 = pool.get_connection()
            assert conn2 is not None

            pool.return_connection(conn2)
            pool.close_pool()
        finally:
            os.unlink(db_path)


class TestGlobalConnectionPool:

    def setUp(self):
        """Clean up any existing global pool."""
        close_connection_pool()

    def tearDown(self):
        """Clean up global pool after test."""
        close_connection_pool()

    def test_initialize_global_pool(self):
        """Test global connection pool initialization."""
        self.setUp()

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            # Initialize pool
            pool = initialize_connection_pool(db_path, pool_size=5)

            assert pool.pool_size == 5

            # Get same pool instance
            pool2 = get_connection_pool()
            assert pool is pool2

            # Test get_db context manager
            with get_db() as conn:
                conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
                conn.execute("INSERT INTO test (id) VALUES (1)")

                result = conn.execute("SELECT COUNT(*) as count FROM test").fetchone()
                assert result["count"] == 1

            # Test pool stats
            stats = get_pool_stats()
            assert "pool_size" in stats
            assert stats["pool_size"] == 5

        finally:
            self.tearDown()
            os.unlink(db_path)

    def test_auto_initialization(self):
        """Test auto-initialization of global pool."""
        self.setUp()

        # First call should auto-initialize with defaults
        pool = get_connection_pool()
        assert pool is not None
        assert pool.pool_size == 20  # Updated default size

        self.tearDown()


def test_performance_comparison():
    """
    Performance test comparing direct connections vs connection pool.
    This test demonstrates the performance benefits of connection pooling.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        # Setup test table
        import sqlite3

        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE perf_test (id INTEGER PRIMARY KEY, data TEXT)")

        num_operations = 100

        # Test 1: Direct connections (old method)
        start_time = time.time()
        for i in range(num_operations):
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("INSERT INTO perf_test (data) VALUES (?)", (f"direct_{i}",))
            conn.commit()
            conn.close()
        direct_time = time.time() - start_time

        # Test 2: Connection pool
        pool = SQLiteConnectionPool(db_path, pool_size=5)
        start_time = time.time()
        for i in range(num_operations):
            with pool.connection() as conn:
                conn.execute("INSERT INTO perf_test (data) VALUES (?)", (f"pool_{i}",))
        pool_time = time.time() - start_time
        pool.close_pool()

        print(f"Direct connections: {direct_time:.3f}s")
        print(f"Connection pool: {pool_time:.3f}s")
        print(f"Pool is {direct_time/pool_time:.1f}x faster")

        # Pool should be faster or at least comparable
        # Note: For small SQLite operations, the difference may be minimal
        # but for larger workloads with concurrent access, pooling shows clear benefits
        assert pool_time <= direct_time * 1.5  # Allow some margin for test variability

    finally:
        os.unlink(db_path)


if __name__ == "__main__":
    # Run performance comparison
    test_performance_comparison()
