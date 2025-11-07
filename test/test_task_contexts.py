import sqlite3

from app.repository.plan_repository import PlanRepository
from app.repository.plan_storage import get_plan_db_path


def _query_context_columns(plan_id: int, task_id: int):
    path = get_plan_db_path(plan_id)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT context_combined, context_sections, context_meta
            FROM tasks
            WHERE id=?
            """,
            (task_id,),
        ).fetchone()
    return row


def test_context_fields_update(plan_repo: PlanRepository):
    plan = plan_repo.create_plan("Context Inline Plan")
    plan_id = plan.id
    node = plan_repo.create_task(plan_id, name="Node A")

    plan_repo.update_task(
        plan_id,
        node.id,
        context_combined="初始上下文",
        context_sections=[{"title": "背景", "content": "初始"}],
        context_meta={"version": 1},
    )

    reloaded = plan_repo.get_node(plan_id, node.id)
    assert reloaded.context_combined == "初始上下文"
    assert reloaded.context_sections == [{"title": "背景", "content": "初始"}]
    assert reloaded.context_meta == {"version": 1}
    assert reloaded.context_updated_at is not None

    row = _query_context_columns(plan_id, node.id)
    assert row["context_combined"] == "初始上下文"

    plan_repo.update_task(
        plan_id,
        node.id,
        context_combined="更新后的上下文",
        context_sections=[{"title": "更新", "content": "第二版"}],
        context_meta={"version": 2},
    )

    refreshed = plan_repo.get_node(plan_id, node.id)
    assert refreshed.context_combined == "更新后的上下文"
    assert refreshed.context_sections == [{"title": "更新", "content": "第二版"}]
    assert refreshed.context_meta == {"version": 2}

    row = _query_context_columns(plan_id, node.id)
    assert row["context_combined"] == "更新后的上下文"


def test_context_removed_with_task(plan_repo: PlanRepository):
    plan = plan_repo.create_plan("Context Inline Cascade")
    plan_id = plan.id
    node = plan_repo.create_task(plan_id, name="Leaf")

    plan_repo.update_task(
        plan_id,
        node.id,
        context_combined="leaf context",
        context_sections=[],
        context_meta={"keep": False},
    )

    plan_repo.delete_task(plan_id, node.id)

    path = get_plan_db_path(plan_id)
    with sqlite3.connect(path) as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE id=?",
            (node.id,),
        ).fetchone()[0]
    assert remaining == 0
