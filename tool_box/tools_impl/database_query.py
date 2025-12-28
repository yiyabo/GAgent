"""
Database Query Tool Implementation

This module provides database query functionality for AI agents.
"""

import asyncio
import logging
import sqlite3
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SQLiteConnectionPool:
    """Simple SQLite connection pool for better performance"""

    def __init__(self, max_connections: int = 10):
        self.max_connections = max_connections
        self.connections: List[sqlite3.Connection] = []
        self.in_use: set = set()
        self._lock = threading.Lock()

    def get_connection(self, database: str) -> sqlite3.Connection:
        """Get a connection from the pool"""
        with self._lock:
            # Try to reuse existing connection
            for conn in self.connections:
                if id(conn) not in self.in_use:
                    # Check if connection is still valid
                    try:
                        conn.execute("SELECT 1")
                        self.in_use.add(id(conn))
                        return conn
                    except sqlite3.Error:
                        # Connection is invalid, remove it
                        self.connections.remove(conn)
                        continue

            # Create new connection if pool not full
            if len(self.connections) < self.max_connections:
                conn = sqlite3.connect(database, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                # Enable WAL mode for better concurrency
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA cache_size=10000")
                conn.execute("PRAGMA temp_store=MEMORY")

                self.connections.append(conn)
                self.in_use.add(id(conn))
                return conn

            # Pool is full, create temporary connection
            conn = sqlite3.connect(database)
            conn.row_factory = sqlite3.Row
            return conn

    def return_connection(self, conn: sqlite3.Connection) -> None:
        """Return a connection to the pool"""
        with self._lock:
            if id(conn) in self.in_use:
                self.in_use.remove(id(conn))

    def close_all(self) -> None:
        """Close all connections in the pool"""
        with self._lock:
            for conn in self.connections:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            self.connections.clear()
            self.in_use.clear()


# Global connection pool
_connection_pool = SQLiteConnectionPool()


@asynccontextmanager
async def get_db_connection(database: str):
    """Async context manager for database connections"""
    conn = None
    try:
        # Get connection from pool
        conn = await asyncio.get_event_loop().run_in_executor(None, _connection_pool.get_connection, database)
        yield conn
    finally:
        if conn:
            # Return connection to pool
            await asyncio.get_event_loop().run_in_executor(None, _connection_pool.return_connection, conn)


def _normalize_database_path(database: str) -> str:
    """Normalize database path, avoid creating files in root directory"""
    import os
    
    # If relative path without directory separator, put it in temp directory
    if not os.path.dirname(database) and not database.startswith('/'):
        # Create temp directory
        temp_dir = "data/databases/temp"
        os.makedirs(temp_dir, exist_ok=True)
        return os.path.join(temp_dir, database)
    
    # If absolute path or already contains directory, keep as is
    return database


async def database_query_handler(
    database: str, sql: str, operation: str = "query", params: Optional[List[Any]] = None
) -> Dict[str, Any]:
    """
    Database query tool handler

    Args:
        database: Database file path or connection string
        sql: SQL query string
        operation: Operation type ("query", "execute", "schema")
        params: Query parameters for parameterized queries

    Returns:
        Dict containing query results
    """
    try:
        # Normalize database path
        database = _normalize_database_path(database)
        if operation == "query":
            return await _execute_query(database, sql, params)
        elif operation == "execute":
            return await _execute_statement(database, sql, params)
        elif operation == "schema":
            return await _get_schema(database)
        else:
            return {"operation": operation, "success": False, "error": f"Unsupported operation: {operation}"}

    except Exception as e:
        logger.error(f"Database operation failed: {e}")
        return {"operation": operation, "database": database, "success": False, "error": str(e)}


async def _execute_query(database: str, sql: str, params: Optional[List[Any]] = None) -> Dict[str, Any]:
    """Execute SELECT query using connection pool"""
    try:
        # Security check: if querying pending tasks without session_id filter, log warning
        if ("tasks" in sql.lower() and "status" in sql.lower() and "pending" in sql.lower()
            and "session_id" not in sql.lower() and "SELECT" in sql.upper()):
            logger.warning(f"Detected SQL query that may violate task isolation principle: {sql}")
            logger.warning("Suggestion: Pending task queries should include session_id filter condition")
        
        async with get_db_connection(database) as conn:
            cursor = conn.cursor()

            try:
                # Execute query
                if params:
                    cursor.execute(sql, params)
                else:
                    cursor.execute(sql)

                # Fetch results
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description] if cursor.description else []

                # Convert rows to dicts
                results = []
                for row in rows:
                    results.append(dict(row))

                return {
                    "operation": "query",
                    "database": database,
                    "sql": sql,
                    "success": True,
                    "columns": columns,
                    "rows": results,
                    "row_count": len(results),
                }

            finally:
                cursor.close()

    except Exception as e:
        return {"operation": "query", "database": database, "sql": sql, "success": False, "error": str(e)}


async def _execute_statement(database: str, sql: str, params: Optional[List[Any]] = None) -> Dict[str, Any]:
    """Execute INSERT, UPDATE, DELETE statements using connection pool"""
    try:
        async with get_db_connection(database) as conn:
            cursor = conn.cursor()

            try:
                # Execute statement
                if params:
                    cursor.execute(sql, params)
                else:
                    cursor.execute(sql)

                # Commit changes
                conn.commit()

                return {
                    "operation": "execute",
                    "database": database,
                    "sql": sql,
                    "success": True,
                    "rows_affected": cursor.rowcount,
                }

            finally:
                cursor.close()

    except Exception as e:
        return {"operation": "execute", "database": database, "sql": sql, "success": False, "error": str(e)}


async def _get_schema(database: str) -> Dict[str, Any]:
    """Get database schema information using connection pool"""
    try:
        async with get_db_connection(database) as conn:
            cursor = conn.cursor()

            try:
                # Get all tables
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = cursor.fetchall()

                schema_info = {}

                for (table_name,) in tables:
                    # Get table schema
                    cursor.execute(f"PRAGMA table_info({table_name})")
                    columns = cursor.fetchall()

                    # Get sample data
                    try:
                        cursor.execute(f"SELECT * FROM {table_name} LIMIT 3")
                        sample_rows = cursor.fetchall()
                        sample_data = []
                        for row in sample_rows:
                            sample_data.append(list(row))
                    except Exception:
                        sample_data = []

                    schema_info[table_name] = {
                        "columns": [
                            {
                                "name": col[1],
                                "type": col[2],
                                "nullable": not col[3],
                                "default": col[4],
                                "primary_key": bool(col[5]),
                            }
                            for col in columns
                        ],
                        "sample_data": sample_data,
                        "row_count": len(sample_data),
                    }

                return {"operation": "schema", "database": database, "success": True, "tables": schema_info}

            finally:
                cursor.close()

    except Exception as e:
        return {"operation": "schema", "database": database, "success": False, "error": str(e)}


# Tool definition for database query
database_query_tool = {
    "name": "database_query",
    "description": "Execute database queries and operations",
    "category": "data_access",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "database": {"type": "string", "description": "Database file path"},
            "sql": {"type": "string", "description": "SQL query statement"},
            "operation": {
                "type": "string",
                "description": "Operation type",
                "enum": ["query", "execute", "schema"],
                "default": "query",
            },
            "params": {
                "type": "array",
                "description": "Query parameters (for parameterized queries)",
                "items": {"type": ["string", "number", "boolean", "null"]},
            },
        },
        "required": ["database", "sql"],
    },
    "handler": database_query_handler,
    "tags": ["database", "sql", "query", "data"],
    "examples": ["Query user table data", "Count sales records", "Get database schema information"],
}
