"""
共享的pytest fixtures和配置
统一管理测试环境和警告处理
"""

import pytest
import os
import sys
import tempfile
import shutil
import sqlite3
import warnings

# Add the project root to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# 配置测试环境
os.environ['LLM_MOCK'] = '1'
os.environ['DB_PATH'] = ':memory:'
os.environ['EMBEDDING_CACHE_PERSISTENT'] = '0'  # 禁用持久化缓存以避免文件警告

# 配置警告处理
@pytest.fixture(autouse=True)
def configure_test_warnings():
    """自动配置测试警告"""
    # 在每个测试之前配置警告
    with warnings.catch_warnings():
        # 忽略已知的无害警告
        warnings.filterwarnings("ignore", category=DeprecationWarning, module="pkg_resources.*")
        warnings.filterwarnings("ignore", category=DeprecationWarning, module="setuptools.*")
        warnings.filterwarnings("ignore", category=ResourceWarning)
        warnings.filterwarnings("ignore", category=PendingDeprecationWarning, module="asyncio.*")
        
        # 对我们自己的代码保持警告敏感
        warnings.filterwarnings("always", category=DeprecationWarning, module="app.*")
        warnings.filterwarnings("always", category=DeprecationWarning, module="tests.*")
        
        yield


@pytest.fixture
def temp_db():
    """提供临时数据库的fixture"""
    fd, temp_path = tempfile.mkstemp(suffix='.db')
    try:
        os.close(fd)
        yield temp_path
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


@pytest.fixture
def temp_dir():
    """提供临时目录的fixture"""
    temp_path = tempfile.mkdtemp()
    try:
        yield temp_path
    finally:
        shutil.rmtree(temp_path, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """设置测试环境"""
    # 确保测试不会干扰生产数据
    original_db_path = os.environ.get('DB_PATH')
    
    try:
        os.environ['DB_PATH'] = ':memory:'
        yield
    finally:
        if original_db_path:
            os.environ['DB_PATH'] = original_db_path
        elif 'DB_PATH' in os.environ:
            del os.environ['DB_PATH']


@pytest.fixture
def mock_llm_config():
    """提供模拟LLM配置"""
    original_mock = os.environ.get('LLM_MOCK')
    os.environ['LLM_MOCK'] = '1'
    
    try:
        yield
    finally:
        if original_mock:
            os.environ['LLM_MOCK'] = original_mock
        elif 'LLM_MOCK' in os.environ:
            del os.environ['LLM_MOCK']


@pytest.fixture
def suppress_warnings():
    """提供警告抑制上下文"""
    def _suppress(*categories):
        class WarningsSuppressor:
            def __enter__(self):
                self.warnings_context = warnings.catch_warnings()
                self.warnings_context.__enter__()
                for category in categories:
                    warnings.simplefilter("ignore", category)
                return self
            
            def __exit__(self, exc_type, exc_val, exc_tb):
                self.warnings_context.__exit__(exc_type, exc_val, exc_tb)
        
        return WarningsSuppressor()
    
    return _suppress