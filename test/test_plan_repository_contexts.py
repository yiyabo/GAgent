import sqlite3

from app.repository.plan_repository import PlanRepository
from app.repository.plan_storage import get_plan_db_path


def _fetch_task_row(plan_id: int, task_id: int):
    path = get_plan_db_path(plan_id)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT context_combined, context_sections, context_meta, execution_result FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
    return row


def test_context_persistence(plan_repo: PlanRepository):
    plan = plan_repo.create_plan("Context Plan")
    plan_id = plan.id
    node = plan_repo.create_task(plan_id, name="Node")

    plan_repo.update_task(
        plan_id,
        node.id,
        context_combined="initial",
        context_sections=[{"title": "A", "content": "B"}],
        context_meta={"v": 1},
    )

    refreshed = plan_repo.get_node(plan_id, node.id)
    assert refreshed.context_combined == "initial"
    assert refreshed.context_sections == [{"title": "A", "content": "B"}]
    assert refreshed.context_meta == {"v": 1}
    assert refreshed.status == "pending"

    row = _fetch_task_row(plan_id, node.id)
    assert row["context_combined"] == "initial"
    assert row["execution_result"] is None

    plan_repo.update_task(
        plan_id,
        node.id,
        context_combined="updated",
        context_sections=[{"title": "C", "content": "D"}],
        context_meta={"v": 2},
    )

    refreshed = plan_repo.get_node(plan_id, node.id)
    assert refreshed.context_combined == "updated"
    assert refreshed.context_sections == [{"title": "C", "content": "D"}]
    assert refreshed.context_meta == {"v": 2}
    assert refreshed.status == "pending"


def test_context_removed_with_task(plan_repo: PlanRepository):
    plan = plan_repo.create_plan("Context Cascade")
    plan_id = plan.id
    node = plan_repo.create_task(plan_id, name="Leaf")

    plan_repo.update_task(
        plan_id,
        node.id,
        context_combined="context",
        context_sections=[],
        context_meta={"keep": False},
    )

    plan_repo.delete_task(plan_id, node.id)

    row = _fetch_task_row(plan_id, node.id)
    assert row is None
