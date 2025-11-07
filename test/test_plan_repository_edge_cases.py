import os
from pathlib import Path

import pytest

from app.repository.plan_repository import PlanRepository
from app.repository.plan_storage import get_plan_db_path


def test_task_position_and_resequence(plan_repo: PlanRepository):
    plan = plan_repo.create_plan("Position Plan")
    plan_id = plan.id

    parent = plan_repo.create_task(plan_id, name="Parent")
    first = plan_repo.create_task(plan_id, name="Child A", parent_id=parent.id)
    second = plan_repo.create_task(plan_id, name="Child B", parent_id=parent.id)
    third = plan_repo.create_task(plan_id, name="Child C", parent_id=parent.id)

    assert (first.position, second.position, third.position) == (0, 1, 2)

    plan_repo.delete_task(plan_id, second.id)

    reloaded = plan_repo.get_plan_tree(plan_id)
    child_ids = reloaded.children_ids(parent.id)
    assert child_ids == [first.id, third.id]
    assert reloaded.get_node(first.id).position == 0
    assert reloaded.get_node(third.id).position == 1


def test_move_task_reparent_and_root(plan_repo: PlanRepository):
    plan = plan_repo.create_plan("Move Plan")
    plan_id = plan.id

    root_a = plan_repo.create_task(plan_id, name="Root A")
    root_b = plan_repo.create_task(plan_id, name="Root B")
    child = plan_repo.create_task(plan_id, name="Child", parent_id=root_a.id)
    grandchild = plan_repo.create_task(plan_id, name="Grandchild", parent_id=child.id)

    moved = plan_repo.move_task(plan_id, grandchild.id, new_parent_id=root_b.id, new_position=0)
    assert moved.parent_id == root_b.id
    assert moved.depth == 1
    assert moved.path == f"/{root_b.id}/{grandchild.id}"

    promoted = plan_repo.move_task(plan_id, child.id, new_parent_id=None, new_position=0)
    assert promoted.parent_id is None
    assert promoted.depth == 0
    assert promoted.path == f"/{child.id}"


def test_dependency_deduplication(plan_repo: PlanRepository):
    plan = plan_repo.create_plan("Dependency Plan")
    plan_id = plan.id

    root = plan_repo.create_task(plan_id, name="Root")
    dep_a = plan_repo.create_task(plan_id, name="Dep A")
    dep_b = plan_repo.create_task(plan_id, name="Dep B")

    node = plan_repo.create_task(
        plan_id,
        name="Target",
        parent_id=root.id,
        dependencies=[dep_a.id, dep_a.id, "invalid", dep_b.id],
    )
    assert set(node.dependencies) == {dep_a.id, dep_b.id}

    updated = plan_repo.update_task(
        plan_id,
        node.id,
        dependencies=[dep_b.id, "invalid", dep_b.id],
    )
    assert set(updated.dependencies) == {dep_b.id}


def test_update_task_metadata_without_touching_dependencies(plan_repo: PlanRepository):
    plan = plan_repo.create_plan("Metadata Plan")
    plan_id = plan.id

    dep = plan_repo.create_task(plan_id, name="Dep")
    node = plan_repo.create_task(
        plan_id,
        name="Node",
        dependencies=[dep.id],
        metadata={"info": "legacy"},
    )

    updated = plan_repo.update_task(
        plan_id,
        node.id,
        metadata={"info": "migrated"},
    )
    assert updated.metadata == {"info": "migrated"}
    assert updated.dependencies == [dep.id]

    same = plan_repo.update_task(plan_id, node.id)
    assert same.id == node.id
    assert same.dependencies == [dep.id]
    assert same.metadata == {"info": "migrated"}


def test_error_branches(plan_repo: PlanRepository):
    plan = plan_repo.create_plan("Error Plan")
    plan_id = plan.id
    node = plan_repo.create_task(plan_id, name="Base Node")

    with pytest.raises(ValueError):
        plan_repo.subgraph(plan_id, node_id=999, max_depth=2)

    with pytest.raises(ValueError):
        plan_repo.delete_task(plan_id, task_id=999)


def test_missing_plan_file_raises(plan_repo: PlanRepository):
    plan = plan_repo.create_plan("Missing File Plan")
    plan_id = plan.id

    plan_path = get_plan_db_path(plan_id)
    os.remove(plan_path)

    with pytest.raises(ValueError) as exc_info:
        plan_repo.get_plan_tree(plan_id)
    assert "Plan storage not found" in str(exc_info.value)


def test_create_task_with_anchor_positions(plan_repo: PlanRepository):
    plan = plan_repo.create_plan("Anchor Plan")
    plan_id = plan.id

    parent = plan_repo.create_task(plan_id, name="Chapter")
    child_a = plan_repo.create_task(plan_id, name="Section A", parent_id=parent.id)
    child_b = plan_repo.create_task(plan_id, name="Section B", parent_id=parent.id)
    child_c = plan_repo.create_task(plan_id, name="Section C", parent_id=parent.id)

    inserted_before = plan_repo.create_task(
        plan_id,
        name="Intro",
        parent_id=parent.id,
        anchor_task_id=child_b.id,
        anchor_position="before",
    )
    assert inserted_before.parent_id == parent.id

    inserted_after = plan_repo.create_task(
        plan_id,
        name="Appendix",
        parent_id=parent.id,
        anchor_task_id=child_b.id,
        anchor_position="after",
    )
    assert inserted_after.parent_id == parent.id

    inserted_first = plan_repo.create_task(
        plan_id,
        name="Preface",
        parent_id=parent.id,
        anchor_position="first_child",
    )

    inserted_last = plan_repo.create_task(
        plan_id,
        name="Closing",
        parent_id=parent.id,
        anchor_position="last_child",
    )

    tree = plan_repo.get_plan_tree(plan_id)
    order = [tree.get_node(cid).name for cid in tree.children_ids(parent.id)]
    assert order == [
        "Preface",
        "Section A",
        "Intro",
        "Section B",
        "Appendix",
        "Section C",
        "Closing",
    ]


def test_create_task_anchor_validation(plan_repo: PlanRepository):
    plan = plan_repo.create_plan("Anchor Validation")
    plan_id = plan.id

    alpha = plan_repo.create_task(plan_id, name="Alpha")
    beta = plan_repo.create_task(plan_id, name="Beta")
    child = plan_repo.create_task(plan_id, name="Child", parent_id=alpha.id)

    with pytest.raises(ValueError):
        plan_repo.create_task(
            plan_id,
            name="Invalid Anchor",
            parent_id=alpha.id,
            anchor_task_id=beta.id,
            anchor_position="before",
        )

    with pytest.raises(ValueError):
        plan_repo.create_task(
            plan_id,
            name="Missing Anchor",
            parent_id=alpha.id,
            anchor_position="before",
        )
