import os
from typing import List, Dict, Any
import tempfile

from app.database import init_db
from app.repository.tasks import SqliteTaskRepository
from app.services.recursive_decomposition import (
    evaluate_task_complexity, determine_task_type, should_decompose_task,
    decompose_task, recursive_decompose_plan, TaskType
)


def test_evaluate_task_complexity():
    """Test task complexity evaluation based on keywords and content."""
    
    # High complexity tasks
    assert evaluate_task_complexity("构建完整的微服务架构系统", "设计和实现一个完整的微服务架构") == "high"
    assert evaluate_task_complexity("开发端到端的平台框架", "") == "high"
    
    # Medium complexity tasks  
    assert evaluate_task_complexity("实现用户认证模块", "开发用户登录和权限管理功能") == "medium"
    assert evaluate_task_complexity("优化数据库查询性能", "") == "medium"
    
    # Low complexity tasks
    assert evaluate_task_complexity("修复登录按钮样式", "调整CSS样式") == "low"
    assert evaluate_task_complexity("更新API文档", "") == "low"


def test_determine_task_type():
    """Test task type determination based on depth and complexity."""
    
    # Root level tasks
    root_task_high = {"depth": 0, "name": "构建完整系统", "task_type": "atomic"}
    assert determine_task_type(root_task_high, "high") == TaskType.ROOT
    
    root_task_medium = {"depth": 0, "name": "实现功能模块", "task_type": "atomic"}  
    assert determine_task_type(root_task_medium, "medium") == TaskType.COMPOSITE
    
    root_task_low = {"depth": 0, "name": "修复bug", "task_type": "atomic"}
    assert determine_task_type(root_task_low, "low") == TaskType.ATOMIC
    
    # Child tasks (without complexity, should use existing task_type)
    child_task = {"depth": 1, "name": "子任务", "task_type": "composite"}
    assert determine_task_type(child_task) == TaskType.COMPOSITE
    
    deep_task = {"depth": 2, "name": "深层任务", "task_type": "atomic"}
    assert determine_task_type(deep_task) == TaskType.ATOMIC
    
    # Explicit task types
    explicit_root = {"depth": 0, "name": "任务", "task_type": "root"}
    assert determine_task_type(explicit_root) == TaskType.ROOT


def test_should_decompose_task(tmp_path, monkeypatch):
    """Test decomposition decision logic."""
    test_db = tmp_path / "decomp_test.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)
    
    init_db()
    repo = SqliteTaskRepository()
    
    # Create test tasks
    root_task_id = repo.create_task("[TEST] 构建系统", task_type="root")
    composite_task_id = repo.create_task("[TEST] 实现模块", task_type="composite")  
    atomic_task_id = repo.create_task("[TEST] 修复bug", task_type="atomic")
    
    # Deep task (should not decompose due to depth limit)
    deep_parent = repo.create_task("[TEST] 深层父任务", task_type="composite")
    deep_child = repo.create_task("[TEST] 深层子任务", parent_id=deep_parent, task_type="composite")
    deep_grandchild = repo.create_task("[TEST] 深层孙任务", parent_id=deep_child, task_type="composite")
    
    # Test decomposition decisions
    root_task = repo.get_task_info(root_task_id)
    assert should_decompose_task(root_task, repo) == True
    
    composite_task = repo.get_task_info(composite_task_id)
    assert should_decompose_task(composite_task, repo) == True
    
    atomic_task = repo.get_task_info(atomic_task_id)
    assert should_decompose_task(atomic_task, repo) == False
    
    # Deep task should not decompose due to depth limit
    deep_task = repo.get_task_info(deep_grandchild)
    assert should_decompose_task(deep_task, repo) == False
    
    # Task with existing children should not decompose
    child1 = repo.create_task("[TEST] 子任务1", parent_id=composite_task_id)
    child2 = repo.create_task("[TEST] 子任务2", parent_id=composite_task_id)
    
    composite_task_updated = repo.get_task_info(composite_task_id)
    assert should_decompose_task(composite_task_updated, repo) == False


def test_decompose_task_success(tmp_path, monkeypatch):
    """Test successful task decomposition."""
    test_db = tmp_path / "decomp_success.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)
    
    # Mock LLM response for planning service
    def mock_propose_plan(prompt):
        return {
            "success": True,
            "tasks": [
                {"name": "[TEST] 子任务1", "priority": 10, "prompt": "实现第一个功能"},
                {"name": "[TEST] 子任务2", "priority": 20, "prompt": "实现第二个功能"},
                {"name": "[TEST] 子任务3", "priority": 30, "prompt": "实现第三个功能"}
            ]
        }
    
    monkeypatch.setattr("app.services.recursive_decomposition.propose_plan_service", mock_propose_plan)
    
    init_db()
    repo = SqliteTaskRepository()
    
    # Create root task for decomposition
    root_task_id = repo.create_task("[TEST] 构建完整系统", task_type="root")
    repo.upsert_task_input(root_task_id, "构建一个完整的Web应用系统")
    
    # Decompose the task
    result = decompose_task(root_task_id, repo, max_subtasks=5)
    
    # Verify decomposition success
    assert result["success"] == True
    assert result["task_id"] == root_task_id
    assert len(result["subtasks"]) == 3
    assert result["decomposition_depth"] == 1
    
    # Verify subtasks were created
    children = repo.get_children(root_task_id)
    assert len(children) == 3
    
    for i, child in enumerate(children):
        assert child["name"] == f"[TEST] 子任务{i+1}"
        assert child["parent_id"] == root_task_id
        assert child["depth"] == 1
        assert child["task_type"] == "composite"  # Root task children are composite


def test_decompose_task_failure_cases(tmp_path, monkeypatch):
    """Test task decomposition failure scenarios."""
    test_db = tmp_path / "decomp_failure.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)
    
    init_db()
    repo = SqliteTaskRepository()
    
    # Test non-existent task
    result = decompose_task(99999, repo)
    assert result["success"] == False
    assert "not found" in result["error"]
    
    # Test atomic task (should not decompose)
    atomic_task_id = repo.create_task("[TEST] 原子任务", task_type="atomic")
    result = decompose_task(atomic_task_id, repo)
    assert result["success"] == False
    assert "does not need decomposition" in result["error"]
    
    # Test task with existing children
    parent_task_id = repo.create_task("[TEST] 父任务", task_type="composite")
    child1 = repo.create_task("[TEST] 已有子任务1", parent_id=parent_task_id)
    child2 = repo.create_task("[TEST] 已有子任务2", parent_id=parent_task_id)
    
    result = decompose_task(parent_task_id, repo)
    assert result["success"] == False
    assert "does not need decomposition" in result["error"]


def test_recursive_decompose_plan(tmp_path, monkeypatch):
    """Test recursive plan decomposition."""
    test_db = tmp_path / "recursive_plan.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)
    
    # Mock LLM response
    def mock_propose_plan(payload):
        goal = payload.get("goal", "") if isinstance(payload, dict) else str(payload)
        if "构建系统" in goal:
            return {
                "title": "Mock Plan",
                "tasks": [
                    {"name": "[SYSTEM] 前端模块", "priority": 10, "prompt": "开发前端界面"},
                    {"name": "[SYSTEM] 后端模块", "priority": 20, "prompt": "开发后端API"}
                ]
            }
        elif "前端模块" in goal:
            return {
                "title": "Mock Plan",
                "tasks": [
                    {"name": "[SYSTEM] 用户界面", "priority": 10, "prompt": "设计用户界面"},
                    {"name": "[SYSTEM] 交互逻辑", "priority": 20, "prompt": "实现交互逻辑"}
                ]
            }
        else:
            return {"title": "Mock Plan", "tasks": []}
    
    monkeypatch.setattr("app.services.recursive_decomposition.propose_plan_service", mock_propose_plan)
    
    init_db()
    repo = SqliteTaskRepository()
    
    # Create plan with root task
    root_task_id = repo.create_task("[SYSTEM] 构建系统", task_type="root")
    repo.upsert_task_input(root_task_id, "构建完整的Web系统")
    
    # Recursively decompose plan
    result = recursive_decompose_plan("SYSTEM", repo, max_depth=2)
    
    # Verify results
    assert result["success"] == True
    assert result["plan_title"] == "SYSTEM"
    assert result["total_tasks_decomposed"] >= 1
    
    # Verify task hierarchy was created
    all_tasks = repo.list_plan_tasks("SYSTEM")
    root_tasks = [t for t in all_tasks if t.get("depth") == 0]
    child_tasks = [t for t in all_tasks if t.get("depth") == 1]
    
    assert len(root_tasks) >= 1  # At least one root task
    assert len(child_tasks) >= 2  # At least the decomposed children


def test_task_type_integration(tmp_path, monkeypatch):
    """Test task type updates during decomposition."""
    test_db = tmp_path / "type_integration.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)
    
    # Mock LLM response
    def mock_propose_plan(prompt):
        return {
            "success": True,
            "tasks": [
                {"name": "[TYPE] 子任务A", "priority": 10, "prompt": "实现A功能"},
                {"name": "[TYPE] 子任务B", "priority": 20, "prompt": "实现B功能"}
            ]
        }
    
    monkeypatch.setattr("app.services.recursive_decomposition.propose_plan_service", mock_propose_plan)
    
    init_db()
    repo = SqliteTaskRepository()
    
    # Create task with different types
    root_task_id = repo.create_task("[TYPE] 根任务", task_type="root")
    composite_task_id = repo.create_task("[TYPE] 复合任务", task_type="composite")
    
    # Test decomposition with different parent types
    result1 = decompose_task(root_task_id, repo)
    assert result1["success"] == True
    
    children1 = repo.get_children(root_task_id)
    for child in children1:
        assert child["task_type"] == "composite"  # Root children are composite
    
    result2 = decompose_task(composite_task_id, repo)
    assert result2["success"] == True
    
    children2 = repo.get_children(composite_task_id)
    for child in children2:
        assert child["task_type"] == "composite"  # Composite children are also composite at depth 1


def test_complexity_evaluation_edge_cases():
    """Test edge cases in complexity evaluation."""
    
    # Empty inputs
    assert evaluate_task_complexity("", "") == "medium"  # Default to medium
    
    # Mixed complexity keywords
    mixed_text = "系统重构和bug修复"  # High + Low keywords
    complexity = evaluate_task_complexity(mixed_text, "")
    assert complexity in ["high", "medium", "low"]  # Should handle gracefully
    
    # Very long description (should tend toward high complexity)
    long_desc = "实现" + "功能" * 50  # Long but medium keywords
    assert evaluate_task_complexity("长任务", long_desc) == "medium"
    
    # Very short description (should tend toward low complexity)
    short_desc = "修复"
    assert evaluate_task_complexity(short_desc, "") == "low"
