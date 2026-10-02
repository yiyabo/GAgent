"""Track descendant identities and clean them on both normal and forced exits."""
import os
from pathlib import Path
import signal
import subprocess
import time
import sqlite3


def process_table():
    records = {}
    proc = Path('/proc')
    if proc.exists():
        for path in proc.iterdir():
            if not path.name.isdigit():
                continue
            try:
                stat = (path / 'stat').read_text().split(') ', 1)[1].split()
                if stat[0] != 'Z':
                    records[int(path.name)] = (int(stat[1]), stat[19])
            except (OSError, ValueError, IndexError):
                continue
    else:
        output = subprocess.run(['ps', '-axo', 'pid=,ppid=,lstart='], text=True, capture_output=True, check=True).stdout
        for line in output.splitlines():
            parts = line.split(None, 2)
            if len(parts) == 3:
                records[int(parts[0])] = (int(parts[1]), parts[2])
    return records


def descendants(pid):
    records = process_table()
    family = {pid}
    while True:
        children = {child for child, (parent, _) in records.items() if parent in family}
        if children <= family:
            break
        family |= children
    return {child: records[child][1] for child in family - {pid}}


def cleanup_children(children):
    children = {int(pid): marker for pid, marker in children.items()}
    if not children:
        return {'tracked_children': 0, 'signalled_children': [], 'remaining_children': [],
                'tracking_scope': 'observed_descendants_with_start_identity'}
    killed = []
    for sig in (signal.SIGTERM, signal.SIGKILL):
        current = process_table()
        for pid, marker in children.items():
            if current.get(pid, (None, None))[1] != marker:
                continue
            try:
                os.kill(pid, sig)
                killed.append(pid)
            except ProcessLookupError:
                pass
        if killed and sig == signal.SIGTERM:
            time.sleep(.1)
    current = process_table()
    remaining = [pid for pid, marker in children.items() if current.get(pid, (None, None))[1] == marker]
    deadline = time.monotonic() + 1
    while remaining and time.monotonic() < deadline:
        time.sleep(.05)
        current = process_table()
        remaining = [pid for pid in remaining if current.get(pid, (None, None))[1] == children[pid]]
    return {'tracked_children': len(children), 'signalled_children': sorted(set(killed)),
            'remaining_children': remaining, 'tracking_scope': 'observed_descendants_with_start_identity'}


def stop(process, children, grace=10):
    children.update(descendants(process.pid))
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    return cleanup_children(children)


def close_isolated_runs(root):
    """Reap only evaluation-owned rows in this trial's isolated database."""
    database = Path(root) / 'db_root/main/plan_registry.db'
    if not database.exists():
        return {'database_present': False}
    from app.services.chat_run_state import transition_chat_run_status
    with sqlite3.connect(database) as con:
        con.row_factory = sqlite3.Row
        present = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_runs'").fetchone()
        if not present:
            return {'database_present': True, 'run_table_present': False}
        rows = con.execute("SELECT run_id,worker_id,status FROM chat_runs WHERE owner_id='harness-eval'").fetchall()
        for row in rows:
            if row['status'] in {'queued', 'running'}:
                transition_chat_run_status(con, row['run_id'], 'failed', error='evaluation_supervisor_cleanup')
            con.execute("UPDATE chat_runs SET lease_expires_at=NULL WHERE run_id=? AND worker_id IS ?", (row['run_id'], row['worker_id']))
        remaining = con.execute("SELECT COUNT(*) FROM chat_runs WHERE owner_id='harness-eval' AND (status IN ('queued','running') OR lease_expires_at IS NOT NULL)").fetchone()[0]
    return {'database_present': True, 'remaining_active_eval_runs': remaining}
