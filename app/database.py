import sqlite3
from contextlib import contextmanager
from .database_pool import initialize_connection_pool, get_db

DB_PATH = 'tasks.db'

def init_db():
    """Initialize database schema using connection pool."""
    # Initialize connection pool first
    initialize_connection_pool(db_path=DB_PATH)
    
    # Use pooled connection for schema initialization
    with get_db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            status TEXT,
            priority INTEGER DEFAULT 100
        )''')
        # Backfill priority column for existing databases created before this change
        try:
            conn.execute('ALTER TABLE tasks ADD COLUMN priority INTEGER DEFAULT 100')
        except Exception:
            # Ignore if the column already exists or ALTER is not applicable
            pass
        # Hierarchy columns (Option B): parent_id, path, depth
        try:
            conn.execute('ALTER TABLE tasks ADD COLUMN parent_id INTEGER')
        except Exception:
            # Column may already exist
            pass
        try:
            conn.execute('ALTER TABLE tasks ADD COLUMN path TEXT')
        except Exception:
            pass
        try:
            conn.execute('ALTER TABLE tasks ADD COLUMN depth INTEGER DEFAULT 0')
        except Exception:
            pass
        # Phase 6: Task type for recursive decomposition (root/composite/atomic)
        try:
            conn.execute('ALTER TABLE tasks ADD COLUMN task_type TEXT DEFAULT "atomic"')
        except Exception:
            pass
        # Stores the prompt/input for each task
        conn.execute('''CREATE TABLE IF NOT EXISTS task_inputs (
            task_id INTEGER UNIQUE,
            prompt TEXT
        )''')
        # Stores the generated content/output for each task
        conn.execute('''CREATE TABLE IF NOT EXISTS task_outputs (
            task_id INTEGER UNIQUE,
            content TEXT
        )''')

        # Phase 1/2/3: Graph links and context snapshots tables (ensure existence for indexes)
        conn.execute('''CREATE TABLE IF NOT EXISTS task_links (
            from_id INTEGER,
            to_id INTEGER,
            kind TEXT,
            PRIMARY KEY (from_id, to_id, kind)
        )''')

        conn.execute('''CREATE TABLE IF NOT EXISTS task_contexts (
            task_id INTEGER,
            label TEXT,
            combined TEXT,
            sections TEXT,
            meta TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (task_id, label)
        )''')

        # Backfill hierarchy values for existing rows
        try:
            conn.execute("UPDATE tasks SET path = '/' || id WHERE path IS NULL")
        except Exception:
            pass
        try:
            conn.execute("UPDATE tasks SET depth = 0 WHERE depth IS NULL")
        except Exception:
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
        conn.execute('''CREATE TABLE IF NOT EXISTS task_embeddings (
            task_id INTEGER PRIMARY KEY,
            embedding_vector TEXT NOT NULL,
            embedding_model TEXT DEFAULT 'embedding-2',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE
        )''')
        
        # Index for embeddings table
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_embeddings_model ON task_embeddings(embedding_model)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_embeddings_created_at ON task_embeddings(created_at)")

        # Evaluation System Tables
        conn.execute('''CREATE TABLE IF NOT EXISTS evaluation_history (
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
        )''')
        
        conn.execute('''CREATE TABLE IF NOT EXISTS evaluation_configs (
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
        )''')
        
        # Indexes for evaluation tables
        conn.execute("CREATE INDEX IF NOT EXISTS idx_evaluation_history_task_id ON evaluation_history(task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_evaluation_history_iteration ON evaluation_history(task_id, iteration)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_evaluation_history_timestamp ON evaluation_history(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_evaluation_configs_task_id ON evaluation_configs(task_id)")

# get_db function now provided by database_pool module