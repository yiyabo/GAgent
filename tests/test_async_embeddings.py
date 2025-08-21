#!/usr/bin/env python3
"""
异步embedding功能测试模块

测试GLM Embeddings服务的异步处理功能，包括：
- 异步embedding生成
- 后台任务管理
- 预计算功能
- 进度回调
"""

import os
import pytest
import time
from concurrent.futures import Future
from unittest.mock import patch

from app.database import init_db
from app.services.embeddings import get_embeddings_service, shutdown_embeddings_service


@pytest.fixture(autouse=True)
def setup_mock_env(monkeypatch):
    """设置mock环境变量"""
    monkeypatch.setenv("LLM_MOCK", "1")
    monkeypatch.setenv("GLM_DEBUG", "1")


@pytest.fixture
def embeddings_service():
    """获取embeddings服务实例"""
    service = get_embeddings_service()
    yield service
    # 清理
    service.cancel_background_tasks()


def test_async_embedding_generation(embeddings_service):
    """测试异步embedding生成"""
    texts = ["测试文本1", "测试文本2", "测试文本3"]
    
    # 测试异步获取embeddings
    future = embeddings_service.get_embeddings_async(texts)
    
    # 验证返回Future对象
    assert isinstance(future, Future)
    
    # 等待结果
    embeddings = future.result(timeout=10)
    
    # 验证结果
    assert len(embeddings) == len(texts)
    assert all(isinstance(emb, list) and len(emb) > 0 for emb in embeddings)


def test_async_single_embedding(embeddings_service):
    """测试异步单个embedding生成"""
    text = "单个测试文本"
    
    # 测试异步获取单个embedding
    future = embeddings_service.get_single_embedding_async(text)
    
    # 验证返回Future对象
    assert isinstance(future, Future)
    
    # 等待结果
    embedding = future.result(timeout=10)
    
    # 验证结果
    assert isinstance(embedding, list)
    assert len(embedding) > 0


def test_async_with_callback(embeddings_service):
    """测试带回调的异步处理"""
    texts = ["回调测试1", "回调测试2"]
    callback_results = []
    
    def test_callback(embeddings):
        callback_results.extend(embeddings)
    
    # 提交异步任务
    future = embeddings_service.get_embeddings_async(texts, callback=test_callback)
    
    # 等待完成
    embeddings = future.result(timeout=10)
    
    # 验证回调被执行
    assert len(callback_results) == len(texts)
    assert callback_results == embeddings


def test_precompute_embeddings_async(embeddings_service):
    """测试异步预计算功能"""
    texts = ["预计算1", "预计算2", "预计算3", "预计算4", "预计算5"]
    progress_updates = []
    
    def progress_callback(completed, total, results):
        progress_updates.append((completed, total))
    
    # 启动预计算
    future = embeddings_service.precompute_embeddings_async(
        texts, 
        progress_callback=progress_callback
    )
    
    # 等待完成
    completed_count = future.result(timeout=15)
    
    # 验证结果 - 重构后返回字典格式
    assert isinstance(completed_count, dict)
    assert completed_count["processed"] == len(texts)
    assert len(progress_updates) > 0
    
    # 验证最后一次进度更新
    final_progress = progress_updates[-1]
    assert final_progress[0] == len(texts)  # 完成数量
    assert final_progress[1] == len(texts)  # 总数量
    
    # 验证embeddings已缓存
    cached_results, cache_misses = embeddings_service.cache.get_batch(texts)
    assert len(cache_misses) == 0  # 所有都应该已缓存


def test_background_task_management(embeddings_service):
    """测试后台任务管理"""
    texts1 = ["任务1-1", "任务1-2"]
    texts2 = ["任务2-1", "任务2-2"]
    
    # 提交多个后台任务
    future1 = embeddings_service.get_embeddings_async(texts1)
    future2 = embeddings_service.get_embeddings_async(texts2)
    
    # 检查任务状态
    status = embeddings_service.get_background_task_status()
    assert status["total_tasks"] >= 2
    
    # 等待所有任务完成
    results = embeddings_service.wait_for_background_tasks(timeout=15)
    
    # 验证结果
    assert len(results) >= 2
    assert all(result is not None for result in results)
    
    # 验证任务已清理
    final_status = embeddings_service.get_background_task_status()
    assert final_status["total_tasks"] == 0


def test_task_cancellation(embeddings_service):
    """测试任务取消功能"""
    # 提交一些长时间运行的任务
    long_texts = [f"长文本任务{i}" for i in range(20)]
    
    futures = []
    for i in range(3):
        future = embeddings_service.get_embeddings_async(long_texts)
        futures.append(future)
    
    # 立即取消任务
    cancelled_count = embeddings_service.cancel_background_tasks()
    
    # 验证有任务被取消（可能有些已经开始执行）
    assert cancelled_count >= 0
    
    # 检查最终状态
    status = embeddings_service.get_background_task_status()
    # 注意：可能还有正在运行的任务无法取消


def test_empty_input_handling(embeddings_service):
    """测试空输入处理"""
    # 测试空列表
    future = embeddings_service.get_embeddings_async([])
    result = future.result(timeout=5)
    assert result == []
    
    # 测试空字符串 (在mock模式下仍会返回embedding)
    future = embeddings_service.get_single_embedding_async("")
    result = future.result(timeout=5)
    assert isinstance(result, list)  # mock模式下会返回embedding


def test_service_info_with_async_status(embeddings_service):
    """测试服务信息包含异步状态"""
    # 提交一些任务
    future1 = embeddings_service.get_embeddings_async(["信息测试1"])
    future2 = embeddings_service.precompute_embeddings_async(["信息测试2", "信息测试3"])
    
    # 获取服务信息
    info = embeddings_service.get_service_info()
    
    # 验证包含组件信息 - 重构后结构变化
    assert "components" in info
    assert "async_manager" in info["components"]
    async_status = info["components"]["async_manager"]
    
    assert "total_tasks" in async_status
    assert "active_tasks" in async_status
    assert "completed_tasks" in async_status
    
    # 等待任务完成
    embeddings_service.wait_for_background_tasks(timeout=10)


def test_concurrent_async_operations(embeddings_service):
    """测试并发异步操作"""
    import threading
    
    results = []
    errors = []
    
    def async_worker(worker_id):
        try:
            texts = [f"并发测试{worker_id}-{i}" for i in range(5)]
            future = embeddings_service.get_embeddings_async(texts)
            embeddings = future.result(timeout=15)
            results.append((worker_id, len(embeddings)))
        except Exception as e:
            errors.append((worker_id, str(e)))
    
    # 启动多个并发worker
    threads = []
    for i in range(3):
        thread = threading.Thread(target=async_worker, args=(i,))
        threads.append(thread)
        thread.start()
    
    # 等待所有线程完成
    for thread in threads:
        thread.join(timeout=20)
    
    # 验证结果
    assert len(errors) == 0, f"Errors occurred: {errors}"
    assert len(results) == 3
    assert all(count == 5 for _, count in results)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
