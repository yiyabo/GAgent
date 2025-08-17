import pytest

from app.database import init_db
from app.repository.tasks import SqliteTaskRepository


def _setup_repo(tmp_path, monkeypatch) -> SqliteTaskRepository:
    test_db = tmp_path / "hierarchy.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)
    init_db()
    return SqliteTaskRepository()


def test_hierarchy_basic_crud(tmp_path, monkeypatch):
    repo = _setup_repo(tmp_path, monkeypatch)

    # Create root and descendants: A -> B -> C
    a = repo.create_task("A", status="pending", priority=1)
    b = repo.create_task("B", status="pending", priority=2, parent_id=a)
    c = repo.create_task("C", status="pending", priority=3, parent_id=b)

    # Root info
    ai = repo.get_task_info(a)
    assert ai["parent_id"] is None
    assert ai["depth"] == 0
    assert ai["path"] == f"/{a}"

    # Child info
    bi = repo.get_task_info(b)
    assert bi["parent_id"] == a
    assert bi["depth"] == 1
    assert bi["path"] == f"/{a}/{b}"

    ci = repo.get_task_info(c)
    assert ci["parent_id"] == b
    assert ci["depth"] == 2
    assert ci["path"] == f"/{a}/{b}/{c}"

    # Parent/children
    pc = repo.get_parent(c)
    assert pc and pc["id"] == b

    ch_a = repo.get_children(a)
    assert [x["id"] for x in ch_a] == [b]

    # Ancestors/descendants/subtree
    an_c = repo.get_ancestors(c)
    assert [x["id"] for x in an_c] == [a, b]

    de_a = repo.get_descendants(a)
    assert [x["id"] for x in de_a] == [b, c]

    st_a = repo.get_subtree(a)
    assert [x["id"] for x in st_a] == [a, b, c]


def test_hierarchy_move_and_cycle_prevention(tmp_path, monkeypatch):
    repo = _setup_repo(tmp_path, monkeypatch)

    # A -> B -> C
    a = repo.create_task("A")
    b = repo.create_task("B", parent_id=a)
    c = repo.create_task("C", parent_id=b)

    # Cycle prevention: cannot move A under C (A's subtree contains B->C)
    with pytest.raises(ValueError):
        repo.update_task_parent(a, c)

    # Move B to root
    repo.update_task_parent(b, None)

    bi = repo.get_task_info(b)
    assert bi["parent_id"] is None
    assert bi["depth"] == 0
    assert bi["path"] == f"/{b}"

    ci = repo.get_task_info(c)
    assert ci["parent_id"] == b
    assert ci["depth"] == 1
    assert ci["path"] == f"/{b}/{c}"

    # Move C under A
    repo.update_task_parent(c, a)
    ci2 = repo.get_task_info(c)
    assert ci2["parent_id"] == a
    assert ci2["depth"] == 1
    assert ci2["path"] == f"/{a}/{c}"

    st_a = repo.get_subtree(a)
    assert [x["id"] for x in st_a] == [a, c]


def test_children_order_by_priority(tmp_path, monkeypatch):
    repo = _setup_repo(tmp_path, monkeypatch)

    a = repo.create_task("A")
    # Create children with different priorities (order by priority asc, then id)
    b = repo.create_task("B", priority=20, parent_id=a)
    c = repo.create_task("C", priority=10, parent_id=a)

    children = repo.get_children(a)
    assert [x["id"] for x in children] == [c, b]
