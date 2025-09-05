import sqlite3
from contextlib import contextmanager

DB_PATH = 'tasks.db'

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_priority_id ON tasks(priority, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_parent_id ON tasks(parent_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_path ON tasks(path)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_depth ON tasks(depth)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_type ON tasks(task_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_inputs_task_id ON task_inputs(task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_outputs_task_id ON task_outputs(task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_links_to_id ON task_links(to_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_links_from_id ON task_links(from_id)")
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

        # Plan Management System Tables
        conn.execute('''CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL UNIQUE,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'active',
            config_json TEXT
        )''')
        
        conn.execute('''CREATE TABLE IF NOT EXISTS plan_tasks (
            plan_id INTEGER,
            task_id INTEGER,
            task_category TEXT,
            task_order INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (plan_id, task_id),
            FOREIGN KEY (plan_id) REFERENCES plans(id) ON DELETE CASCADE,
            FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
        )''')

        # Plan system indexes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_plans_title ON plans(title)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_plans_status ON plans(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_plan_tasks_plan_id ON plan_tasks(plan_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_plan_tasks_task_id ON plan_tasks(task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_plan_tasks_order ON plan_tasks(plan_id, task_order)")

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

        # Chat history table (Old schema, still used by some legacy endpoints)
        conn.execute('''CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            sender TEXT NOT NULL, -- 'user' or 'agent'
            message TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (plan_id) REFERENCES plans(id) ON DELETE CASCADE
        )''')
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_plan_id ON chat_messages(plan_id)")

        # New Chat System Tables (supporting multiple conversations per plan)
        conn.execute('''CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (plan_id) REFERENCES plans(id) ON DELETE CASCADE
        )''')
        conn.execute("CREATE INDEX IF NOT EXISTS idx_conversations_plan_id ON conversations(plan_id)")

        conn.execute('''CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            sender TEXT NOT NULL, -- 'user' or 'agent'
            text TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        )''')
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id)")


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()