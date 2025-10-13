"""
Database Connection Pool Implementation

Provides efficient connection pooling for SQLite database operations
to replace the current pattern of creating new connections for each operation.
"""

import logging
import queue
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class SQLiteConnectionPool:
    """
    Thread-safe SQLite connection pool.

    SQLite has some limitations with concurrent access, but connection pooling
    still helps by:
    1. Reducing connection creation/teardown overhead
    2. Managing connection lifecycle properly
    3. Providing consistent connection configuration
    """

    def __init__(
        self,
        db_path: str,
        pool_size: int = 5,  # Optimized for SQLite - reduced from 20
        max_overflow: int = 3,  # Reduced from 10 - SQLite works better with fewer connections
        timeout: float = 30.0,
    ):
        """
        Initialize connection pool.

        Args:
            db_path: Path to SQLite database file
            pool_size: Number of connections to maintain in pool
            max_overflow: Additional connections allowed beyond pool_size
            timeout: Timeout for getting connection from pool
        """
        self.db_path = db_path
        self.pool_size = pool_size
        self.max_overflow = max_overflow
        self.timeout = timeout

        # Thread-safe connection pool
        self._pool = queue.Queue(maxsize=pool_size)
        self._overflow_count = 0
        self._lock = threading.Lock()

        # Initialize pool with connections
        self._initialize_pool()

        logger.info(f"SQLite connection pool initialized: {pool_size} connections for {db_path}")

    def _initialize_pool(self):
        """Initialize the connection pool with base connections."""
        for _ in range(self.pool_size):
            conn = self._create_connection()
            self._pool.put(conn)

    def _create_connection(self) -> sqlite3.Connection:
        """Create a properly configured SQLite connection."""
        # Ensure directory exists
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,  # Allow connection sharing across threads
            timeout=self.timeout,
            isolation_level=None,  # Autocommit mode
        )
        conn.row_factory = sqlite3.Row

        # Enable WAL mode for better concurrent access
        conn.execute("PRAGMA journal_mode=WAL")
        # Enable foreign key constraints
        conn.execute("PRAGMA foreign_keys=ON")
        # Optimize for performance with enhanced settings
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=10000")  # Reduced to reasonable size
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA mmap_size=268435456")  # 256MB - more reasonable than 30GB
        conn.execute("PRAGMA page_size=4096")  # Optimize page size
        conn.execute("PRAGMA busy_timeout=5000")  # Wait up to 5 seconds when database is locked

        return conn

    def get_connection(self) -> sqlite3.Connection:
        """
        Get a connection from the pool.

        Returns:
            SQLite connection from pool or newly created if pool exhausted

        Raises:
            TimeoutError: If no connection available within timeout
        """
        try:
            # Try to get connection from pool (non-blocking)
            return self._pool.get_nowait()
        except queue.Empty:
            # Pool is empty, check if we can create overflow connection
            with self._lock:
                if self._overflow_count < self.max_overflow:
                    self._overflow_count += 1
                    logger.debug(f"Creating overflow connection ({self._overflow_count}/{self.max_overflow})")
                    return self._create_connection()

            # Pool exhausted and max overflow reached, wait for connection
            try:
                conn = self._pool.get(timeout=self.timeout)
                logger.debug("Retrieved connection from pool after waiting")
                return conn
            except queue.Empty:
                raise TimeoutError(f"Could not get connection within {self.timeout} seconds")

    def return_connection(self, conn: sqlite3.Connection, is_overflow: bool = False):
        """
        Return a connection to the pool.

        Args:
            conn: Connection to return
            is_overflow: Whether this is an overflow connection
        """
        if is_overflow:
            # Close overflow connections instead of returning to pool
            with self._lock:
                self._overflow_count -= 1
            conn.close()
            logger.debug("Closed overflow connection")
        else:
            try:
                # Return connection to pool if there's space
                self._pool.put_nowait(conn)
            except queue.Full:
                # Pool is full, close the connection
                conn.close()
                logger.debug("Pool full, closed connection")

    @contextmanager
    def connection(self):
        """
        Context manager for getting and returning connections.

        Usage:
            with pool.connection() as conn:
                cursor = conn.execute("SELECT * FROM table")
        """
        conn = None
        is_overflow = False

        try:
            # Track if this is an overflow connection
            initial_overflow = self._overflow_count
            conn = self.get_connection()
            is_overflow = self._overflow_count > initial_overflow

            yield conn

        except Exception as e:
            logger.error(f"Database operation failed: {e}")
            if conn:
                # Rollback any pending transaction
                try:
                    conn.rollback()
                except:
                    pass
            raise
        finally:
            if conn:
                self.return_connection(conn, is_overflow)

    def close_pool(self):
        """Close all connections in the pool."""
        logger.info("Closing connection pool...")

        # Close all connections in pool
        while not self._pool.empty():
            try:
                conn = self._pool.get_nowait()
                conn.close()
            except queue.Empty:
                break

        logger.info("Connection pool closed")

    def get_stats(self) -> dict:
        """Get pool statistics."""
        return {
            "pool_size": self.pool_size,
            "available_connections": self._pool.qsize(),
            "overflow_connections": self._overflow_count,
            "max_overflow": self.max_overflow,
        }


# Global connection pool instance
_connection_pool: Optional[SQLiteConnectionPool] = None
_pool_lock = threading.Lock()


def initialize_connection_pool(
    db_path: str = "tasks.db",
    pool_size: int = 5,  # Optimized default pool size for SQLite
    max_overflow: int = 3,  # Reduced default overflow
    timeout: float = 30.0,
) -> SQLiteConnectionPool:
    """
    Initialize the global connection pool.

    This should be called once at application startup.
    """
    global _connection_pool

    with _pool_lock:
        if _connection_pool is not None:
            logger.warning("Connection pool already initialized, closing existing pool")
            _connection_pool.close_pool()

        _connection_pool = SQLiteConnectionPool(
            db_path=db_path, pool_size=pool_size, max_overflow=max_overflow, timeout=timeout
        )

        return _connection_pool


def get_connection_pool() -> SQLiteConnectionPool:
    """
    Get the global connection pool instance.

    Raises:
        RuntimeError: If pool not initialized
    """
    global _connection_pool

    if _connection_pool is None:
        with _pool_lock:
            if _connection_pool is None:
                # Auto-initialize with default settings
                logger.info("Auto-initializing connection pool with default settings")
                _connection_pool = SQLiteConnectionPool("tasks.db")

    return _connection_pool


@contextmanager
def get_db():
    """
    Get database connection using connection pool.

    This replaces the old get_db() function to use connection pooling.
    Maintains the same interface for backward compatibility.
    """
    pool = get_connection_pool()
    with pool.connection() as conn:
        yield conn


def close_connection_pool():
    """Close the global connection pool."""
    global _connection_pool

    with _pool_lock:
        if _connection_pool:
            _connection_pool.close_pool()
            _connection_pool = None


def get_pool_stats() -> dict:
    """Get connection pool statistics."""
    pool = get_connection_pool()
    return pool.get_stats()
