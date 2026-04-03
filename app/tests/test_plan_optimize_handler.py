import asyncio

from app.services.plans.plan_models import PlanNode, PlanTree
from tool_box.tools_impl.plan_tools import _optimize_plan


class _OptimizeRepoStub:
    def __init__(self, tree: PlanTree) -> None:
        self.tree = tree
        self.created_tasks = []
        self.updated_tasks = []
        self.upsert_notes = []

    def get_plan_tree(self, plan_id: int) -> PlanTree:
        assert plan_id == self.tree.id
        return self.tree

    def create_task(
        self,
        plan_id: int,
        *,
        name: str,
        status: str,
        instruction: str,
        parent_id: int | None,
        dependencies=None,
    ) -> PlanNode:
        next_id = max(self.tree.nodes) + 1
        node = PlanNode(
            id=next_id,
            plan_id=plan_id,
            name=name,
            status=status,
            instruction=instruction,
            parent_id=parent_id,
            dependencies=list(dependencies or []),
        )
        self.tree.nodes[next_id] = node
        self.tree.rebuild_adjacency()
        self.created_tasks.append(node)
        return node

    def update_task(self, plan_id: int, task_id: int, **kwargs) -> None:
        assert plan_id == self.tree.id
        node = self.tree.nodes[task_id]
        for key, value in kwargs.items():
            setattr(node, key, value)
        self.updated_tasks.append((task_id, kwargs))
        self.tree.rebuild_adjacency()

    def delete_task(self, plan_id: int, task_id: int) -> None:
        assert plan_id == self.tree.id
        self.tree.nodes.pop(task_id)
        self.tree.rebuild_adjacency()

    def move_task(self, plan_id: int, task_id: int, *, new_position: int) -> None:
        assert plan_id == self.tree.id
        node = self.tree.nodes[task_id]
        node.position = new_position

    def upsert_plan_tree(self, tree: PlanTree, *, note: str | None = None) -> None:
        self.tree = tree
        self.upsert_notes.append(note)


def _build_tree() -> PlanTree:
    root = PlanNode(
        id=1,
        plan_id=1,
        name="Demo Plan",
        task_type="root",
        metadata={"is_root": True, "task_type": "root"},
        parent_id=None,
        instruction="Original description",
    )
    nodes = {
        1: root,
        2: PlanNode(id=2, plan_id=1, name="Task A", instruction="Original task A", parent_id=1),
        3: PlanNode(id=3, plan_id=1, name="Task B", instruction="Original task B", parent_id=1),
    }
    tree = PlanTree(
        id=1,
        title="Demo Plan",
        description="Original description",
        nodes=nodes,
    )
    tree.rebuild_adjacency()
    return tree


def test_optimize_plan_accepts_nested_updated_fields_alias(monkeypatch) -> None:
    repo = _OptimizeRepoStub(_build_tree())
    monkeypatch.setattr("app.repository.plan_repository.PlanRepository", lambda: repo)

    result = asyncio.run(
        _optimize_plan(
            1,
            [
                {
                    "action": "update_task",
                    "task_id": 2,
                    "updated_fields": {"instruction": "Updated task A"},
                }
            ],
        )
    )

    assert result["success"] is True
    assert result["applied_changes"] == 1
    assert repo.tree.nodes[2].instruction == "Updated task A"


def test_optimize_plan_accepts_description_and_add_task_aliases(monkeypatch) -> None:
    repo = _OptimizeRepoStub(_build_tree())
    monkeypatch.setattr("app.repository.plan_repository.PlanRepository", lambda: repo)

    result = asyncio.run(
        _optimize_plan(
            1,
            [
                {
                    "action": "update_description",
                    "new_description": "Revised plan rationale",
                },
                {
                    "action": "add_task",
                    "task_name": "Robustness Check",
                    "task_instruction": "Test clustering stability across resolutions.",
                    "dependencies": [2],
                },
            ],
        )
    )

    assert result["success"] is True
    assert result["applied_changes"] == 2
    assert repo.tree.description == "Revised plan rationale"
    assert repo.tree.nodes[1].instruction == "Revised plan rationale"
    assert repo.created_tasks[-1].name == "Robustness Check"
    assert repo.created_tasks[-1].instruction == "Test clustering stability across resolutions."


def test_optimize_plan_rejects_empty_update_payload(monkeypatch) -> None:
    repo = _OptimizeRepoStub(_build_tree())
    monkeypatch.setattr("app.repository.plan_repository.PlanRepository", lambda: repo)

    result = asyncio.run(
        _optimize_plan(
            1,
            [
                {
                    "action": "update_task",
                    "task_id": 2,
                    "updated_fields": {"unsupported": "value"},
                }
            ],
        )
    )

    assert result["success"] is False
    assert result["applied_changes"] == 0
    assert result["failed_changes"] == 1
    assert "No supported update fields" in result["changes_detail"]["failed"][0]["error"]
