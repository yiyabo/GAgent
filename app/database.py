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

        # Useful indexes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_name ON tasks(name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_priority_id ON tasks(priority, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_inputs_task_id ON task_inputs(task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_outputs_task_id ON task_outputs(task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_links_to_id ON task_links(to_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_links_from_id ON task_links(from_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_contexts_task_id ON task_contexts(task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_contexts_created_at ON task_contexts(created_at)")

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()