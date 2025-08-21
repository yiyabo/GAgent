#!/usr/bin/env python3

import os
import tempfile
from app.database import init_db
from app.repository.tasks import SqliteTaskRepository
from app.scheduler import bfs_schedule
from app.utils import split_prefix

def debug_bfs_ordering():
    """Debug BFS scheduler ordering to understand the actual behavior."""
    
    # Create temporary database
    with tempfile.TemporaryDirectory() as tmp_dir:
        test_db = os.path.join(tmp_dir, "debug.db")
        os.environ["DB_PATH"] = test_db
        
        # Monkey patch for this debug session
        import app.database
        app.database.DB_PATH = test_db
        
        init_db()
        repo = SqliteTaskRepository()

        # Create hierarchical tasks with mixed priorities
        print("Creating test tasks...")
        a = repo.create_task("[TEST] A", status="pending", priority=5)
        b = repo.create_task("[TEST] B", status="pending", priority=1)
        
        a1 = repo.create_task("[TEST] A1", status="pending", priority=10, parent_id=a)
        a2 = repo.create_task("[TEST] A2", status="pending", priority=2, parent_id=a)
        
        b1 = repo.create_task("[TEST] B1", status="pending", priority=3, parent_id=b)
        b2 = repo.create_task("[TEST] B2", status="pending", priority=8, parent_id=b)

        # Debug: examine raw data
        print("\nRaw task data:")
        all_pending = repo.list_pending_full()
        for task in all_pending:
            print(f"  ID={task['id']}, Name={task['name']}, Priority={task['priority']}, "
                f"Parent={task['parent_id']}, Path={task['path']}, Depth={task['depth']}")

        # Debug: examine BFS ordering
        print("\nBFS ordering:")
        order = list(bfs_schedule())
        for i, task in enumerate(order):
            short_name = split_prefix(task.get("name", ""))[1]
            print(f"  {i}: {short_name} (ID={task['id']}, Priority={task['priority']}, "
                f"Path={task['path']}, Depth={task['depth']})")

        # Debug: examine sorting keys
        print("\nSorting keys:")
        from app.scheduler import _bfs_heap_key
        for task in all_pending:
            key = _bfs_heap_key(task)
            short_name = split_prefix(task.get("name", ""))[1]
            print(f"  {short_name}: {key}")

if __name__ == "__main__":
    debug_bfs_ordering()
