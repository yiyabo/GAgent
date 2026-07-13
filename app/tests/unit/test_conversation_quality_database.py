from __future__ import annotations

from app.database import init_db
from app.database_pool import get_db


def test_quality_evaluation_table_is_initialized(isolated_app_env) -> None:
    _ = isolated_app_env
    init_db()
    with get_db() as conn:
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='conversation_quality_evaluations'"
        ).fetchone()
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(conversation_quality_evaluations)").fetchall()
        }
    assert table is not None
    assert {"target_run_id", "snapshot_json", "evaluation_json", "owner_id"} <= columns
