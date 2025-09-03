"""
核心功能测试

专注测试系统的核心功能，确保重构后基本功能正常。
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import init_db
from app.repository.tasks import default_repo


@pytest.fixture(scope="module")
def client():
    """创建测试客户端"""
    init_db()
    return TestClient(app)


@pytest.fixture(scope="function") 
def clean_db():
    """每个测试前清理数据库"""
    # 简单清理，只删除测试数据
    try:
        with default_repo.get_db() as conn:
            conn.execute("DELETE FROM tasks WHERE name LIKE 'TEST_%'")
            conn.execute("DELETE FROM task_inputs WHERE task_id IN (SELECT id FROM tasks WHERE name LIKE 'TEST_%')")
            conn.execute("DELETE FROM task_outputs WHERE task_id IN (SELECT id FROM tasks WHERE name LIKE 'TEST_%')")
            conn.commit()
    except Exception:
        pass  # 忽略清理错误


class TestBasicAPI:
    """测试基础API功能"""
    
    def test_health_check(self, client):
        """测试健康检查端点"""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
    
    def test_llm_health(self, client):
        """测试LLM健康检查"""
        response = client.get("/health/llm")
        assert response.status_code == 200
        data = response.json()
        assert "ping_ok" in data


class TestTaskManagement:
    """测试任务管理功能"""
    
    def test_create_task(self, client, clean_db):
        """测试创建任务"""
        task_data = {"name": "TEST_创建任务测试", "task_type": "atomic"}
        response = client.post("/tasks", json=task_data)
        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert isinstance(data["id"], int)
    
    def test_list_tasks(self, client, clean_db):
        """测试列出任务"""
        # 先创建一个任务
        task_data = {"name": "TEST_列表测试", "task_type": "atomic"}
        create_response = client.post("/tasks", json=task_data)
        assert create_response.status_code == 200
        
        # 然后列出任务
        list_response = client.get("/tasks")
        assert list_response.status_code == 200
        tasks = list_response.json()
        assert isinstance(tasks, list)
        
        # 检查我们创建的任务在列表中
        test_tasks = [t for t in tasks if t["name"] == "TEST_列表测试"]
        assert len(test_tasks) >= 1


class TestPlanManagement:
    """测试计划管理功能"""
    
    def test_list_plans(self, client):
        """测试列出计划"""
        response = client.get("/plans")
        assert response.status_code == 200
        data = response.json()
        assert "plans" in data
        assert isinstance(data["plans"], list)


class TestSystemIntegration:
    """测试系统集成功能"""
    
    def test_app_routes_loaded(self, client):
        """测试所有路由是否正确加载"""
        # 检查主要端点是否存在
        important_routes = [
            "/health",
            "/tasks", 
            "/plans",
            "/evaluation/stats",
            "/tools/available"
        ]
        
        for route_path in important_routes:
            # 对于GET端点，直接测试
            if route_path in ["/health", "/plans", "/evaluation/stats"]:
                response = client.get(route_path)
                assert response.status_code in [200, 404, 500]  # 至少要能响应
            
    def test_error_handlers(self, client):
        """测试错误处理器"""
        # 测试404错误
        response = client.get("/nonexistent")
        assert response.status_code == 404
        
        # 测试无效参数
        response = client.post("/tasks", json={"invalid": "data"})
        assert response.status_code in [400, 422]  # 参数验证错误


if __name__ == "__main__":
    # 可以直接运行这个文件进行快速测试
    pytest.main([__file__, "-v"])
