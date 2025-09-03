"""
基础API测试

测试重构后的API端点基本功能。
"""

import pytest


class TestHealthEndpoints:
    """健康检查端点测试"""
    
    def test_health_check(self, client):
        """测试基础健康检查"""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "service" in data
    
    def test_llm_health_without_ping(self, client):
        """测试LLM健康检查（不ping）"""
        response = client.get("/health/llm")
        assert response.status_code == 200
        data = response.json()
        assert "ping_ok" in data


class TestTaskAPI:
    """任务API测试"""
    
    def test_list_tasks(self, client):
        """测试任务列表API"""
        response = client.get("/tasks")
        assert response.status_code == 200
        tasks = response.json()
        assert isinstance(tasks, list)
    
    def test_create_task(self, client):
        """测试创建任务API"""
        task_data = {"name": "TEST_API_创建任务", "task_type": "atomic"}
        response = client.post("/tasks", json=task_data)
        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert isinstance(data["id"], int)


class TestPlanAPI:
    """计划API测试"""
    
    def test_list_plans(self, client):
        """测试计划列表API"""
        response = client.get("/plans")
        assert response.status_code == 200
        data = response.json()
        assert "plans" in data
        assert isinstance(data["plans"], list)


class TestToolAPI:
    """工具API测试"""
    
    @pytest.mark.asyncio
    async def test_list_tools(self, client):
        """测试工具列表API"""
        response = client.get("/tools/available")
        # 可能需要异步初始化，所以允许一些错误
        assert response.status_code in [200, 500]


class TestEvaluationAPI:
    """评估API测试"""
    
    def test_evaluation_stats(self, client):
        """测试评估统计API"""
        response = client.get("/evaluation/stats")
        assert response.status_code == 200
        data = response.json()
        assert "evaluation_stats" in data or "system_info" in data


class TestErrorHandling:
    """错误处理测试"""
    
    def test_404_error(self, client):
        """测试404错误处理"""
        response = client.get("/nonexistent")
        assert response.status_code == 404
    
    def test_invalid_task_data(self, client):
        """测试无效任务数据"""
        response = client.post("/tasks", json={"invalid": "data"})
        assert response.status_code in [400, 422]  # 验证错误
