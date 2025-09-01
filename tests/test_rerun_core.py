import os
import sys
import tempfile
from unittest.mock import patch

import pytest

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import init_db
from app.repository.tasks import SqliteTaskRepository


@pytest.fixture
def temp_db():
    """创建临时数据库用于测试"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        with patch("app.database.DB_PATH", db_path):
            init_db()
            yield db_path


@pytest.fixture
def task_repo(temp_db):
    """创建任务仓库实例"""
    return SqliteTaskRepository()


@pytest.fixture
def mock_execute_task():
    """模拟execute_task函数"""
    with patch("app.executor.execute_task") as mock:
        mock.return_value = "completed"
        yield mock


class TestRerunCoreLogic:
    """测试重新执行的核心逻辑"""

    def test_single_task_rerun_logic(self, task_repo, mock_execute_task):
        """测试单个任务重新执行的核心逻辑"""
        # 创建测试任务
        task_id = task_repo.create_task(
            "测试任务", status="completed", task_type="atomic"
        )
        task_repo.upsert_task_output(task_id, "旧输出内容")

        # 模拟重新执行逻辑
        task = task_repo.get_task_info(task_id)
        assert task is not None

        # 重置任务状态
        task_repo.update_task_status(task_id, "pending")

        # 清空任务输出
        task_repo.upsert_task_output(task_id, "")

        # 重新执行
        status = mock_execute_task.return_value
        task_repo.update_task_status(task_id, status)

        # 验证结果
        updated_task = task_repo.get_task_info(task_id)
        assert updated_task["status"] == "completed"

        # 验证输出被清空
        output = task_repo.get_task_output_content(task_id)
        assert output == ""

    def test_subtree_rerun_logic(self, task_repo, mock_execute_task):
        """测试子树重新执行的核心逻辑"""
        # 创建父任务
        parent_id = task_repo.create_task(
            "父任务", status="completed", task_type="composite"
        )

        # 创建子任务
        child1_id = task_repo.create_task(
            "子任务1", parent_id=parent_id, status="completed", task_type="atomic"
        )
        child2_id = task_repo.create_task(
            "子任务2", parent_id=parent_id, status="completed", task_type="atomic"
        )

        # 添加输出
        task_repo.upsert_task_output(parent_id, "父任务旧输出")
        task_repo.upsert_task_output(child1_id, "子任务1旧输出")
        task_repo.upsert_task_output(child2_id, "子任务2旧输出")

        # 获取子树任务
        parent_task = task_repo.get_task_info(parent_id)
        children = task_repo.get_children(parent_id)

        # 收集所有要重新执行的任务
        tasks_to_rerun = [parent_task] + children

        # 按优先级排序
        tasks_to_rerun.sort(key=lambda t: (t.get("priority", 100), t.get("id", 0)))

        # 重新执行所有任务
        results = []
        for task in tasks_to_rerun:
            task_id = task["id"]
            task_repo.update_task_status(task_id, "pending")
            task_repo.upsert_task_output(task_id, "")

            status = mock_execute_task.return_value
            task_repo.update_task_status(task_id, status)

            results.append({"task_id": task_id, "name": task["name"], "status": status})

        # 验证结果
        assert len(results) == 3
        task_ids = [r["task_id"] for r in results]
        assert parent_id in task_ids
        assert child1_id in task_ids
        assert child2_id in task_ids

        # 验证所有任务状态
        for task_result in results:
            assert task_result["status"] == "completed"

    def test_rerun_nonexistent_task(self, task_repo):
        """测试重新执行不存在的任务"""
        nonexistent_id = 99999
        task = task_repo.get_task_info(nonexistent_id)
        assert task is None

    def test_rerun_empty_subtree(self, task_repo, mock_execute_task):
        """测试重新执行没有子任务的子树"""
        task_id = task_repo.create_task(
            "独立任务", status="completed", task_type="atomic"
        )

        # 获取任务
        task = task_repo.get_task_info(task_id)

        # 重新执行
        task_repo.update_task_status(task_id, "pending")
        task_repo.upsert_task_output(task_id, "")

        status = mock_execute_task.return_value
        task_repo.update_task_status(task_id, status)

        # 验证
        updated_task = task_repo.get_task_info(task_id)
        assert updated_task["status"] == "completed"

    def test_rerun_preserves_priority_order(self, task_repo, mock_execute_task):
        """测试重新执行保持优先级顺序"""
        parent_id = task_repo.create_task(
            "父任务", status="completed", task_type="composite"
        )

        # 创建不同优先级的子任务
        high_priority_id = task_repo.create_task(
            "高优先级任务",
            parent_id=parent_id,
            priority=10,
            status="completed",
            task_type="atomic",
        )
        low_priority_id = task_repo.create_task(
            "低优先级任务",
            parent_id=parent_id,
            priority=100,
            status="completed",
            task_type="atomic",
        )

        # 获取所有任务
        parent_task = task_repo.get_task_info(parent_id)
        children = task_repo.get_children(parent_id)

        # 按优先级排序
        tasks_to_rerun = [parent_task] + children
        tasks_to_rerun.sort(key=lambda t: (t.get("priority", 100), t.get("id", 0)))

        # 验证排序 - 按优先级升序排列
        priorities = [t["priority"] for t in tasks_to_rerun]
        assert priorities == [10, 100, 100]  # 高优先级10，低优先级100，父任务默认100

        # 重新执行
        for task in tasks_to_rerun:
            task_repo.update_task_status(task["id"], "pending")
            task_repo.upsert_task_output(task["id"], "")
            task_repo.update_task_status(task["id"], mock_execute_task.return_value)

    def test_rerun_with_dependencies(self, task_repo, mock_execute_task):
        """测试重新执行有依赖关系的任务"""
        # 创建任务和依赖关系
        task1_id = task_repo.create_task(
            "前置任务", status="completed", task_type="atomic"
        )
        task2_id = task_repo.create_task(
            "依赖任务", status="completed", task_type="atomic"
        )

        # 添加依赖关系
        task_repo.create_link(task2_id, task1_id, "requires")

        # 重新执行依赖任务
        task2 = task_repo.get_task_info(task2_id)
        task_repo.update_task_status(task2_id, "pending")
        task_repo.upsert_task_output(task2_id, "")

        status = mock_execute_task.return_value
        task_repo.update_task_status(task2_id, status)

        # 验证
        updated_task = task_repo.get_task_info(task2_id)
        assert updated_task["status"] == "completed"


class TestRerunDataIntegrity:
    """测试重新执行的数据完整性"""

    def test_output_cleanup_on_rerun(self, task_repo, mock_execute_task):
        """测试重新执行时输出被正确清理"""
        task_id = task_repo.create_task(
            "测试任务", status="completed", task_type="atomic"
        )

        # 设置初始输出
        initial_output = "这是初始输出内容\n包含多行文本"
        task_repo.upsert_task_output(task_id, initial_output)

        # 验证初始输出
        assert task_repo.get_task_output_content(task_id) == initial_output

        # 重新执行
        task_repo.update_task_status(task_id, "pending")
        task_repo.upsert_task_output(task_id, "")
        task_repo.update_task_status(task_id, mock_execute_task.return_value)

        # 验证输出被清空
        assert task_repo.get_task_output_content(task_id) == ""

    def test_status_transitions(self, task_repo, mock_execute_task):
        """测试状态转换的正确性"""
        task_id = task_repo.create_task(
            "状态测试任务", status="completed", task_type="atomic"
        )

        # 初始状态
        task = task_repo.get_task_info(task_id)
        assert task["status"] == "completed"

        # 重置为pending
        task_repo.update_task_status(task_id, "pending")
        task = task_repo.get_task_info(task_id)
        assert task["status"] == "pending"

        # 重新执行完成
        task_repo.update_task_status(task_id, mock_execute_task.return_value)
        task = task_repo.get_task_info(task_id)
        assert task["status"] == "completed"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
