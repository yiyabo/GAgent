"""
测试配置和共用固件

提供所有测试共用的配置和固件。
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import init_db


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """设置测试环境"""
    init_db()


@pytest.fixture(scope="module")
def client():
    """FastAPI测试客户端"""
    return TestClient(app)


@pytest.fixture(scope="function")
def clean_test_data():
    """清理测试数据"""
    # 在测试前后清理测试相关数据
    yield
    # 清理逻辑可以在这里添加
