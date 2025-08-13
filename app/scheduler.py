from .repository import tasks as task_repo

def bfs_schedule():
    rows = task_repo.list_tasks_by_status('pending')
    # Ensure stable ordering consistent with previous SQL
    rows_sorted = sorted(
        rows,
        key=lambda r: ((r.get('priority') if isinstance(r, dict) else r[3]) or 100, (r.get('id') if isinstance(r, dict) else r[0]))
    )
    for t in rows_sorted:
        yield t