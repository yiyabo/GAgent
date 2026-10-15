from app.routers.chat.plan_reference import plan_reference_index
from app.services.plans.plan_models import PlanNode, PlanTree


def test_stage_ordinals_use_ui_order_and_preserve_composite_identity():
    # Insertion order and numeric IDs deliberately differ from displayed stages.
    nodes = {}
    for tid, parent, name, pos, kind in [
        (16, 3, "检索", 0, "atomic"), (3, 1, "文献证据库", 2, "composite"),
        (14, 2, "大纲", 1, "atomic"), (2, 1, "定位与大纲", 1, "composite"),
        (1, None, "综述计划", 0, "root"), (13, 2, "定位", 0, "atomic"),
    ]:
        nodes[tid] = PlanNode(id=tid, plan_id=34, parent_id=parent, name=name,
                              position=pos, metadata={"task_type": kind})
    tree = PlanTree(id=34, title="综述", nodes=nodes)
    before = tree.model_dump()
    index = plan_reference_index(tree, referenced_task_id=14)
    assert index["stages"] == [{"number": 1, "task_id": 2, "name": "定位与大纲"},
                               {"number": 2, "task_id": 3, "name": "文献证据库"}]
    assert [n["id"] for n in index["tasks"]] == [1, 2, 13, 14, 3, 16]
    assert index["tasks"][-1]["ancestor_ids"] == [1, 3]
    assert index["tasks"][1]["children_ids"] == [13, 14]
    assert index["referenced_task_id"] == 14
    assert tree.model_dump() == before
    limited = plan_reference_index(tree, limit=3)
    assert limited["truncated"] and len(limited["tasks"]) == 3


def test_incomplete_tree_stays_bounded_and_marks_missing_structure():
    tree = PlanTree(id=3, title="broken", nodes={
        1: PlanNode(id=1, plan_id=3, parent_id=2, name="A"),
        2: PlanNode(id=2, plan_id=3, parent_id=1, name="B"),
    })
    index = plan_reference_index(tree)
    assert {n["id"] for n in index["tasks"]} == {1, 2}
    assert index["stages"] == []
