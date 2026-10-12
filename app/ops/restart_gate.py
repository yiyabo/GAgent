"""Read-only restart gate; unknown database state is never an idle result.

Also executable from stdin inside the running app, so a new host-side gagentctl
can inspect an older image before deploying the new gate module.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
from contextlib import closing


def probe(root: Path) -> dict:
    errors = []

    def count(path, table):
        try:
            with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=3)) as conn:
                return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE status IN ('running','queued')").fetchone()[0]
        except (OSError, sqlite3.Error) as exc:
            errors.append({'database':path.name, 'table':table, 'error':type(exc).__name__})
            return None

    chats = count(root/'main'/'plan_registry.db', 'chat_runs')
    try:
        plans = root/'plans'
        shards = sorted(path for path in plans.iterdir() if path.suffix == '.sqlite') if plans.exists() else []
    except OSError as exc:
        shards = []
        errors.append({'database':'plans', 'error':type(exc).__name__})
    system = root/'jobs'/'system_jobs.sqlite'
    if system.exists():
        shards.append(system)
    counts = [count(path, 'decomposition_jobs') for path in shards]
    jobs = sum(value for value in counts if value is not None) if not errors else None
    state = 'unknown' if errors else 'busy' if chats or jobs else 'idle'
    return {'state':state, 'active_chat_runs':chats, 'active_plan_jobs':jobs,
            'databases_checked':1+len(shards), 'errors':errors}


def main():
    result = probe(Path(os.getenv('DB_ROOT', '/app/data/databases')))
    print(json.dumps(result, ensure_ascii=False))
    return {'idle':0, 'busy':1, 'unknown':2}[result['state']]


if __name__ == '__main__':
    raise SystemExit(main())
