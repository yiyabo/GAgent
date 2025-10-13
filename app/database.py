import sqlite3

from .database_pool import get_db, initialize_connection_pool
from .config.database_config import get_main_database_path


def init_db():
    """Initialize database schema using connection pool."""
    # Get configured database path
    db_path = get_main_database_path()
    
    # Initialize connection pool first
    initialize_connection_pool(db_path=db_path)

    # Use pooled connection for schema initialization
    with get_db() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            status TEXT
        )"""
        )
        # Add columns for both new and existing databases
        # Use specific exception handling for better error management
        columns_to_add = [
            ("priority", "INTEGER DEFAULT 100"),
            ("parent_id", "INTEGER"),
            ("path", "TEXT"),
            ("depth", "INTEGER DEFAULT 0"),
            ("task_type", 'TEXT DEFAULT "atomic"'),
            ("session_id", "TEXT"),
            ("root_id", "INTEGER"),
            ("workflow_id", "TEXT"),
            ("metadata", "TEXT"),
            ("context_refs", "TEXT"),
            ("artifacts", "TEXT"),
            ("created_at", "TIMESTAMP"),  # SQLite doesn't support DEFAULT CURRENT_TIMESTAMP in ALTER TABLE
            ("updated_at", "TIMESTAMP")
        ]
        
        for column_name, column_def in columns_to_add:
            try:
                conn.execute(f"ALTER TABLE tasks ADD COLUMN {column_name} {column_def}")
            except sqlite3.OperationalError:
                # Column already exists, skip
                pass
        # Stores the prompt/input for each task
        conn.execute(
            """CREATE TABLE IF NOT EXISTS task_inputs (
            task_id INTEGER UNIQUE,
            prompt TEXT,
            FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE
        )"""
        )
        # Stores the generated content/output for each task
        conn.execute(
            """CREATE TABLE IF NOT EXISTS task_outputs (
            task_id INTEGER UNIQUE,
            content TEXT,
            FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE
        )"""
        )

        # Phase 1/2/3: Graph links and context snapshots tables (ensure existence for indexes)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS task_links (
            from_id INTEGER,
            to_id INTEGER,
            kind TEXT,
            PRIMARY KEY (from_id, to_id, kind)
        )"""
        )

        conn.execute(
            """CREATE TABLE IF NOT EXISTS task_contexts (
            task_id INTEGER,
            label TEXT,
            combined TEXT,
            sections TEXT,
            meta TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (task_id, label)
        )"""
        )

        # Create conversation sessions table
        conn.execute(
            """CREATE TABLE IF NOT EXISTS chat_sessions (
            id TEXT PRIMARY KEY,
            name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT TRUE
        )"""
        )

        # Workflow registry for root/composite/atomic isolation
        conn.execute(
            """CREATE TABLE IF NOT EXISTS workflows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_id TEXT NOT NULL UNIQUE,
            session_id TEXT,
            root_task_id INTEGER UNIQUE,
            title TEXT,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (root_task_id) REFERENCES tasks (id) ON DELETE SET NULL
        )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_workflows_session ON workflows(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_workflows_root ON workflows(root_task_id)")
        conn.execute(
            """CREATE TRIGGER IF NOT EXISTS trg_workflows_updated_at
            AFTER UPDATE ON workflows
            FOR EACH ROW BEGIN
                UPDATE workflows SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
            END;
        """
        )

        # Execution logs capture per-step outputs for atomic/composite assembly
        conn.execute(
            """CREATE TABLE IF NOT EXISTS task_execution_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            workflow_id TEXT,
            step_type TEXT,
            content TEXT,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE
        )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_execution_logs_task ON task_execution_logs(task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_execution_logs_workflow ON task_execution_logs(workflow_id)")

        # Backfill hierarchy values for existing rows (after all columns are added)
        try:
            conn.execute("UPDATE tasks SET path = '/' || id WHERE path IS NULL")
        except sqlite3.OperationalError:
            # Path column may not exist yet
            pass
        try:
            conn.execute("UPDATE tasks SET depth = 0 WHERE depth IS NULL")
        except sqlite3.OperationalError:
            # Depth column may not exist yet
            pass
        
        # Assign existing tasks to a default session if they don't have one
        try:
            # Create a default session for existing tasks
            conn.execute("""INSERT OR IGNORE INTO chat_sessions (id, name, created_at) 
                           VALUES ('default', 'Legacy Tasks', CURRENT_TIMESTAMP)""")
            
            # Assign existing tasks without session_id to default session
            conn.execute("UPDATE tasks SET session_id = 'default' WHERE session_id IS NULL")
        except sqlite3.OperationalError:
            pass

        # Useful indexes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_name ON tasks(name)")
        # Status filters are heavily used by schedulers and queries
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status_prio_id ON tasks(status, priority, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_priority_id ON tasks(priority, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_parent_id ON tasks(parent_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_path ON tasks(path)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_depth ON tasks(depth)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_type ON tasks(task_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_session_id ON tasks(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_session_status ON tasks(session_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_root_id ON tasks(root_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_root_status ON tasks(root_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_workflow_id ON tasks(workflow_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_workflow_status ON tasks(workflow_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_sessions_active ON chat_sessions(is_active)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_inputs_task_id ON task_inputs(task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_outputs_task_id ON task_outputs(task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_links_to_id ON task_links(to_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_links_from_id ON task_links(from_id)")
        # Composite indexes to accelerate lookups by (to_id, kind) and (from_id, kind)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_links_to_kind ON task_links(to_id, kind)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_links_from_kind ON task_links(from_id, kind)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_contexts_task_id ON task_contexts(task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_contexts_created_at ON task_contexts(created_at)")

        # GLM Embeddings storage: task embeddings for semantic search
        conn.execute(
            """CREATE TABLE IF NOT EXISTS task_embeddings (
            task_id INTEGER PRIMARY KEY,
            embedding_vector TEXT NOT NULL,
            embedding_model TEXT DEFAULT 'embedding-3',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE
        )"""
        )

        # Index for embeddings table
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_embeddings_model ON task_embeddings(embedding_model)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_embeddings_created_at ON task_embeddings(created_at)")

        # Evaluation System Tables
        conn.execute(
            """CREATE TABLE IF NOT EXISTS evaluation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            iteration INTEGER NOT NULL,
            content TEXT NOT NULL,
            overall_score REAL NOT NULL,
            dimension_scores TEXT NOT NULL,
            suggestions TEXT,
            needs_revision BOOLEAN NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            metadata TEXT,
            FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE
        )"""
        )

        conn.execute(
            """CREATE TABLE IF NOT EXISTS evaluation_configs (
            task_id INTEGER PRIMARY KEY,
            quality_threshold REAL DEFAULT 0.8,
            max_iterations INTEGER DEFAULT 3,
            evaluation_dimensions TEXT,
            domain_specific BOOLEAN DEFAULT FALSE,
            strict_mode BOOLEAN DEFAULT FALSE,
            custom_weights TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE
        )"""
        )

        # Indexes for evaluation tables
        conn.execute("CREATE INDEX IF NOT EXISTS idx_evaluation_history_task_id ON evaluation_history(task_id)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_evaluation_history_iteration ON evaluation_history(task_id, iteration)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_evaluation_history_timestamp ON evaluation_history(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_evaluation_configs_task_id ON evaluation_configs(task_id)")


# get_db function now provided by database_pool module
