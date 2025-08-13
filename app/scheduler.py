from .database import get_db

def bfs_schedule():
    with get_db() as conn:
        tasks = conn.execute(
            """
            SELECT id, name, status, priority
            FROM tasks
            WHERE status='pending'
            ORDER BY priority ASC, id ASC
            """
        ).fetchall()
        for t in tasks:
            yield t