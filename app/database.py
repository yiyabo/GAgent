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

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()